import code
import json
import os
from collections import OrderedDict

import torch
from tqdm import tqdm

from bopt.core.utils import increasing_roll_left, increasing_roll_right, forever_generator
from bopt.data.logging.lattice_loggers import LOGGERS

INF = 1e9
DEBUG = False

def morpheme_prediction_lattice_step(args, batch, tokenizer, model, device, eval=False):
    batch = [t.to(device) if isinstance(t, torch.Tensor) else t for t in batch]
    input_ids, pos_ids, input_mask, label_ids, fwd_ids, fwd_ms, lengths, bwd_ids, bwd_ms_c, bwd_lengths, tmask, text = batch
    batch_size, N, M, L = fwd_ids.size()
    mmask, emask = tokenizer.parallel_backward_mask(L, M, device)
    mmask = mmask[None, None, ...].expand(batch_size, 1, *mmask.size())
    emask = emask[None, None, ...].expand(batch_size, 1, *emask.size())

    # expand bwd_ids and bwd_ms
    bwd_ids = (bwd_ids.unsqueeze(2) * emask.to(torch.long) + tokenizer.pad_index * (1-emask.to(torch.long))).reshape(batch_size, N * L, M, L)
    bwd_ms = (bwd_ms_c.unsqueeze(2) * emask + mmask).reshape(batch_size, N * L, M, L)

    # dp lattice if necessasry
    ent, a, m, c = None, None, None, None
    if args.vopt:
        if args.mixture_count > 1:
            ent, a, m, c, ment, _ = tokenizer(fwd_ids, fwd_ms, lengths,
                                     bwd_ids, bwd_ms, bwd_lengths,
                                     mmask, emask, tmask, marginal_temperature=args.marginal_temperature)
        else:
            ent, a, m, c = tokenizer(fwd_ids, fwd_ms, lengths,
                                     bwd_ids, bwd_ms, bwd_lengths,
                                     mmask, emask, tmask, marginal_temperature=args.marginal_temperature)

    # run model
    losses = model(input_ids=input_ids, position_ids=pos_ids, labels=label_ids, attn_bias=a if args.vopt else None, output_attentions=args.log_attention_statistics and eval, mixture_bias=args.mixture_count > 1)
    # get loss
    loss = losses[0] * args.main_loss_multiplier
    logits = losses[1]
    if args.log_attention_statistics and eval:
        attentions = losses["attentions"]
        attentions = torch.stack(attentions, dim=0)
        attentions_protected = torch.clamp(attentions, min=1e-6)
        mask = input_mask[None,:,None,:,None].to(torch.float).expand_as(attentions)
        lattice = a.exp()[None,:,None,:,:] if args.mixture_count == 1 else a.exp().transpose(0,1)[None,...]

        over_attention = (torch.clamp(attentions - lattice, min=0) * mask).sum().item()
        over_attention_count = ((attentions > lattice) * mask).sum().item()
        over_attention_mass = (attentions[((attentions > lattice) * mask).to(torch.bool)]).sum().item()
        leakage = ((1- attentions.sum(-1)) * mask[:,:,:,:,0]).sum().item()
        attention_entropy = (- attentions_protected * torch.log(attentions_protected) * mask).sum(-1)
        attention_entropy_count = (mask[:,:,:,:,0]).sum().item()
        attention_entropy_mean = attention_entropy.sum().item() / attention_entropy_count
        attention_entropy_std = ((((attention_entropy - attention_entropy_mean) * mask[:,:,:,:,0]) ** 2).sum() / attention_entropy_count).sqrt().item()
        astat = {"leakage":leakage,
                 "over_attention_mean": over_attention / over_attention_count if over_attention_count > 0 else 0.0,
                 "over_attention_count": over_attention_count,
                 "over_attention_mass": over_attention_mass,
                 "total_attention_count": mask.sum().item(),
                 "entropy_mean":attention_entropy_mean,
                 "entropy_count": attention_entropy_count,
                 "entropy_std": attention_entropy_std,
                 "entropy": attention_entropy,
                 "mask": mask}
    else:
        astat = {}
    return logits, loss, ent if args.mixture_count == 1 else ment, lengths, None, None, None, None, astat

def morpheme_prediction_unigram_step(args, batch, tokenizer, model, device):
    batch = [t.to(device) if isinstance(t, torch.Tensor) else t for t in batch]
    input_ids, pos_ids, input_mask, label_ids = batch
    losses = model(input_ids=input_ids, position_ids=pos_ids, attention_mask=input_mask, labels=label_ids, attn_bias=None, return_dict=True)
    loss = losses[0] * args.main_loss_multiplier
    logits = losses[1]
    return logits, loss, None, None, None, None, None, None, None


def language_modeling_lattice_step(args, batch, tokenizer, model, device, eval=False, decode=False, decode_remove_csp=True, decode_remove_padding=True, unigram_expert=None, skip_gram=False):
    """

    Args:
        args: command line arguments
        batch: a tuple of batched matrices / lists
        tokenizer: the tokenizer for the input, capable of doing DP to get attention bias etc
        model: the transformer language model
        device: cpu or cuda device name string
        eval: whether this is an evaluation step (influences the loss computation and NOT THE EVAL MODE OF THE MODEL)
        decode: whether to run in decode mode
        decode_remove_csp: whether to remove continuing subword prefixes in decode mode
        decode_remove_padding: whether to remove padding in decode mode

    Returns:
        None
    """

    # expand batch
    batch = [t.to(device) if isinstance(t, torch.Tensor) else t for t in batch]
    # size references:
    #   input_ids/pos_ids/input_mask: [batch_size, N * (E + L)]
    #   fwd_ids, fwd_ms: [batch_size, N, M, L]
    #   lengths: [batch_size, N]
    #   bwd_ids, bwd_ms_c: [batch_size, N, M, L]
    #   bwd_lengths: [batch_size, N * L]
    #   mmask, emask: [batch_size, 1, L, M, L]
    #   binary_mask: [batch_size, N * (E + L)
    #   ntokens: [batch_size, N]
    if skip_gram:
        (input_ids, pos_ids, input_mask,
         fwd_ids, fwd_ms, lengths,
         bwd_ids, bwd_ms_c, bwd_lengths,
         txt) = batch
        _batch_size, _N, _M, _L = fwd_ids.size()
        mmask, emask = tokenizer.parallel_backward_mask(_L, _M, device)
        mmask = mmask[None, None, ...].expand(_batch_size, 1, *mmask.size())
        emask = emask[None, None, ...].expand(_batch_size, 1, *emask.size())
        binary_mask = input_mask
        ntokens = torch.ones((_batch_size, 1), device=device, dtype=torch.long)
    else:
        if args.output_viterbi:
            (input_ids, pos_ids, input_mask,
             fwd_ids, fwd_ms, lengths,
             bwd_ids, bwd_ms_c, bwd_lengths,
             vfwd_ids, vfwd_ms,
             vbwd_ids, vbwd_ms_c,
             mmask, emask, binary_mask, txt, ntokens) = batch
        else:
            (input_ids, pos_ids, input_mask,
             fwd_ids, fwd_ms, lengths,
             bwd_ids, bwd_ms_c, bwd_lengths,
             mmask, emask, binary_mask, txt, ntokens) = batch

    # save some size variables, N is the number of blocks, M is the max edge length, and L is the block size
    batch_size, N, M, L = fwd_ids.size()


    # expand bwd_ids and bwd_ms
    bwd_ids = (bwd_ids.unsqueeze(2) * emask.to(torch.long) + tokenizer.pad_index * (1-emask.to(torch.long))).reshape(batch_size, N * L, M, L)
    bwd_ms = (bwd_ms_c.unsqueeze(2) * emask + mmask).reshape(batch_size, N * L, M, L)

    # build some useful masks
    bwd_connector = (bwd_ids == tokenizer.pad_index).to(torch.float)
    bwd_connector[:, :, 1:, :] = 0  # [batch_size, N, M, L]
    global_mask = (binary_mask.unsqueeze(1) * binary_mask.unsqueeze(2) # [batch_size, N * (E + L), N * (E + L)]
                       * tokenizer.causal_mask(N, L, M, device=device).unsqueeze(0) # [1, N * (E + L), N * (E + L)]
                   )

    # to distinguish between the lattice used for fixed-pointing and the lattice used for computing output loss
    # we use output_xx to denote lattice quantities associated with the fix-pointing
    # and f_output_xx to denote lattice quantities associated with the final output
    if args.output_viterbi:
        assert args.group_lasso == 0
        output_fwd_ids = fwd_ids
        output_fwd_ms = fwd_ms
        output_bwd_ms_c = bwd_ms_c # this is the un-expanded bwd_ms
        output_bwd_ms = bwd_ms

        f_output_fwd_ids = vfwd_ids
        f_output_fwd_ms = vfwd_ms
        f_output_bwd_ms_c = vbwd_ms_c # this is the un-expanded vbwd_ms
        vbwd_ms = (vbwd_ms_c.unsqueeze(2) * emask + mmask).reshape(batch_size, N * L, M, L)
        f_output_bwd_ms = vbwd_ms
        f_output_lengths = lengths
    else:
        f_output_fwd_ids = output_fwd_ids = fwd_ids
        f_output_fwd_ms = output_fwd_ms = fwd_ms
        f_output_bwd_ids = bwd_ids
        f_output_bwd_ms_c = output_bwd_ms_c = bwd_ms_c
        f_output_bwd_ms = output_bwd_ms = bwd_ms
        f_output_lengths = lengths
        f_output_bwd_lengths = bwd_lengths

    # initialize the lattice using the individually parametrized edge weights and perform fix-pointing
    output_fwd_ts = None
    output_bwd_ts = None
    if args.debug_fixed_point:
        assert not args.output_viterbi
        prev = output_fwd_ts
        counter = 0
        errors = []
        model.eval() # dropout messes with fix-pointing so let's turn it off
        with torch.no_grad(): # don't track gradient to save memory
            for _ in forever_generator():
                ent, a, m, c = tokenizer(fwd_ids, fwd_ms, lengths,
                                         bwd_ids, bwd_ms, bwd_lengths,
                                         mmask, emask, None, lm=True, lm_mask=global_mask, fwd_ts=output_fwd_ts, bwd_ts=output_bwd_ts, marginal_temperature=args.marginal_temperature)
                # run model
                losses = model(input_ids=input_ids, position_ids=pos_ids, attn_bias=a if args.vopt else None, return_dict=True, unigram_expert=unigram_expert)

                # get indices
                indices = increasing_roll_left(output_fwd_ids, tokenizer.pad_index).transpose(-1, -2).reshape(batch_size, N * L,M)  # batch x NL x M

                # get output probabilities
                log_edge_weights = torch.log_softmax(losses["logits"][:, -N * L:, :], -1)  # batch x NL x V

                # get log probs and convert back to transition matrix
                output_fwd_ts = torch.gather(log_edge_weights, -1, indices).reshape(batch_size, N, L, M).transpose(-1,-2)  # batch x N x M x L

                # do some masking of the BOS and do some conditioning
                bos_mask = torch.ones_like(output_fwd_ts) # [batch_size, N, M, L]
                bos_mask[:, 0, :, 0] = 0  # first column of first block is bos
                ofts = output_fwd_ts.reshape(batch_size, -1)
                conditioning = ofts.max(-1)[0].detach() # set the BOS edge weight appropriately to protect against over/underflow
                output_fwd_ts = bos_mask * output_fwd_ts + (1 - bos_mask) * conditioning[:, None, None, None]

                # build backward ts for next step
                output_bwd_ts = torch.flip(output_fwd_ts, dims=[-1])
                output_bwd_ts = output_bwd_ts * output_bwd_ms_c + -INF * (1 - output_bwd_ms_c)
                output_bwd_ts = (output_bwd_ts.unsqueeze(2) * emask + -INF * (1 - emask)).reshape(batch_size, N * L, M, L)
                output_bwd_ts = output_bwd_ts * (1 - bwd_connector)

                # continue to re-shape the output_fwd_ts
                output_fwd_ts = increasing_roll_right(output_fwd_ts, 0)
                output_fwd_ts = output_fwd_ts * output_fwd_ms + (1 - output_fwd_ms) * -INF

                # check for convergence to fixed-point
                diff = ((output_fwd_ts - prev)**2).sum().item()
                errors.append(diff)
                if diff < 1e-3: break
                if counter >= 100:
                    print(f"Error: did not converge in 100 iterations {errors}")
                    code.interact(local=locals())
                    break
                prev = output_fwd_ts
                counter += 1
        model.train()
    # perform one fixed-point iteration at the fixed point with gradient and dropout
    if args.debug_fixed_point:
        ent, a, m, c = tokenizer(fwd_ids, fwd_ms, lengths,
                                 bwd_ids, bwd_ms, bwd_lengths,
                                 mmask, emask, None, lm=True, lm_mask=global_mask, fwd_ts=output_fwd_ts, bwd_ts=output_bwd_ts, marginal_temperature=args.marginal_temperature)
        # run model
        losses = model(input_ids=input_ids, position_ids=pos_ids, attn_bias=a if args.vopt else None, return_dict=True, unigram_expert=unigram_expert)

        # get indices
        indices = increasing_roll_left(output_fwd_ids, tokenizer.pad_index).transpose(-1, -2).reshape(batch_size, N * L, M) # batch x NL x M

        # get output probabilities
        log_edge_weights = torch.log_softmax(losses["logits"][:, -N * L:, :], -1) # batch x NL x V

        # get log probs and convert back to transition matrix
        output_fwd_ts = torch.gather(log_edge_weights, -1, indices).reshape(batch_size, N, L, M).transpose(-1,-2) # batch x N x M x L

        # do some masking of the BOS and do some conditioning
        bos_mask = torch.ones_like(output_fwd_ts)
        bos_mask[:,0,:,0] = 0 # first column of first block is bos
        ofts = output_fwd_ts.reshape(batch_size, -1)
        conditioning = ofts.max(-1)[0].detach()
        output_fwd_ts = bos_mask * output_fwd_ts + (1-bos_mask) * conditioning[:,None, None, None]

        # build backward ts for next step
        output_bwd_ts = torch.flip(output_fwd_ts, dims=[-1])
        output_bwd_ts = output_bwd_ts * output_bwd_ms_c + -INF * (1 - output_bwd_ms_c)
        output_bwd_ts = (output_bwd_ts.unsqueeze(2) * emask + -INF * (1-emask)).reshape(batch_size, N * L, M, L)
        output_bwd_ts = output_bwd_ts * (1-bwd_connector)

        # continue to re-shape the output_fwd_ts
        output_fwd_ts = increasing_roll_right(output_fwd_ts, 0)
        output_fwd_ts = output_fwd_ts * output_fwd_ms + (1-output_fwd_ms) * -INF

    # dp lattice if necessasry
    ent, a, m, c = None, None, None, None
    if args.vopt:
        ent, a, m, c = tokenizer(fwd_ids, fwd_ms, lengths,
                                 bwd_ids, bwd_ms, bwd_lengths,
                                 mmask, emask, None, lm=True, lm_mask=global_mask, fwd_ts=output_fwd_ts, bwd_ts=output_bwd_ts, marginal_temperature=args.marginal_temperature)
    # run model
    losses = model(input_ids=input_ids, position_ids=pos_ids, attn_bias=a if args.vopt else None, return_dict=True, unigram_expert=unigram_expert)

    # get indices
    indices = increasing_roll_left(f_output_fwd_ids, tokenizer.pad_index).transpose(-1, -2).reshape(batch_size, N * L, M) # batch x NL x M

    # get output probabilities
    log_edge_weights = torch.log_softmax(losses["logits"][:, -N * L:, :], -1) # batch x NL x V

    # get log probs and convert back to transition matrix
    f_output_fwd_ts = torch.gather(log_edge_weights, -1, indices).reshape(batch_size, N, L, M).transpose(-1,-2) # batch x N x M x L
    bos_mask = torch.ones_like(f_output_fwd_ts)
    bos_mask[:,0,:,0] = 0 # first column of first block is bos
    ofts = f_output_fwd_ts.reshape(batch_size, -1)
    conditioning = ofts.max(-1)[0].detach()
    f_output_fwd_ts = bos_mask * f_output_fwd_ts + (1-bos_mask) * conditioning[:,None, None, None]

    # build backward ts
    f_output_bwd_ts = torch.flip(f_output_fwd_ts, dims=[-1])
    f_output_bwd_ts = f_output_bwd_ts * f_output_bwd_ms_c + -INF * (1 - f_output_bwd_ms_c)
    f_output_bwd_ts = (f_output_bwd_ts.unsqueeze(2) * emask + -INF * (1-emask)).reshape(batch_size, N * L, M, L)
    f_output_bwd_ts = f_output_bwd_ts * (1-bwd_connector)
    # continue to re-shape the f_output_fwd_ts
    f_output_fwd_ts = increasing_roll_right(f_output_fwd_ts, 0)
    f_output_fwd_ts = f_output_fwd_ts * f_output_fwd_ms + (1-f_output_fwd_ms) * -INF

    if skip_gram:
        f_output_fwd_ts = f_output_fwd_ts[:,1:,...]
        f_output_fwd_ids = f_output_fwd_ids[:, 1:, ...]
        f_output_fwd_ms = f_output_fwd_ms[:, 1:, ...]
        f_output_lengths = f_output_lengths[:, 1:]
        f_output_bwd_ts = f_output_bwd_ts[:,1:,...]
        f_output_bwd_ids = f_output_bwd_ids[:, 1:, ...]
        f_output_bwd_ms = f_output_bwd_ms[:, 1:, ...]
        f_output_bwd_lengths = f_output_bwd_lengths[:, 1:, ...]
    # do some logging if requested
    if args.log_lattice and eval:
        with open(os.path.join(args.output_dir, args.log_lattice_file), "at") as f:
            d = OrderedDict()
            d["key"] = args.log_lattice_key
            for field in args.log_lattice:
                value = LOGGERS[field](**locals())
                d[field] = value
            f.write(json.dumps(d) + "\n")

    if decode:
        max_log_alpha, _, backpointers = tokenizer.viterbi_algorithm(f_output_fwd_ts.reshape(-1, *f_output_fwd_ts.size()[2:]), f_output_fwd_ms.reshape(-1, *f_output_fwd_ms.size()[2:]), f_output_lengths.reshape(-1, *f_output_lengths.size()[2:]))
        word_ids = tokenizer.decode_backpointers(f_output_fwd_ids.reshape(-1, *f_output_fwd_ids.size()[2:]), f_output_lengths.reshape(-1, *f_output_lengths.size()[2:]), backpointers)
        word_ids = [sum(word_ids[i*N:(i+1)*N],[]) for i in range(batch_size)]
        return [[tokenizer.id2str(id, remove_csp=decode_remove_csp) for id in word_id if not decode_remove_padding or not tokenizer.is_padding(id)] for word_id in word_ids]

    # compute prob
    if not eval or not args.eval_viterbi_mode:
        log_alphas, _ = tokenizer.forward_algorithm(f_output_fwd_ts.reshape(-1, M, L), # batch x N, M, L or batch x N - 1 ... for skipgram
                                                    f_output_fwd_ms.reshape(-1, M, L),  # batch x N, M, L or batch x N - 1 ... for skipgram
                                                    f_output_lengths.reshape(-1)) # batch x N
    else:
        log_alphas, _, _ = tokenizer.viterbi_algorithm(f_output_fwd_ts.reshape(-1, M, L),
                                                    # batch x N, M, L or batch x N - 1 ... for skipgram
                                                    f_output_fwd_ms.reshape(-1, M, L),
                                                    # batch x N, M, L or batch x N - 1 ... for skipgram
                                                    f_output_lengths.reshape(-1))  # batch x N
    log_probs = log_alphas.sum()
    nchars = f_output_lengths.sum() - batch_size # adjust for BOS

    if eval:
        loss = -log_probs / nchars  * args.main_loss_multiplier
    elif args.normalize_by_tokens:
        loss = -log_probs / (f_output_fwd_ms.sum() - batch_size) * args.main_loss_multiplier
    elif args.normalize_by_expected_length:
        length_transition = (torch.ones(f_output_fwd_ids.size(-2)))[None,None, :, None].expand(*f_output_fwd_ids.size()).to(f_output_fwd_ms.device) * f_output_fwd_ms
        expected_lengths, _ = tokenizer.expectation(f_output_fwd_ts.reshape(-1, M, L), length_transition.reshape(-1, M, L), f_output_fwd_ms.reshape(batch_size * N, M, L), f_output_lengths.reshape(-1)) # lengths is reshaped to batch x N or batch-1 x N for skipgram
        loss = -log_probs / (expected_lengths.sum().item() - batch_size) * args.main_loss_multiplier
    elif args.no_normalization:
        loss = -log_probs * args.main_loss_multiplier / batch_size
    elif args.constant_normalization:
        loss = -log_probs * args.main_loss_multiplier / batch_size / args.constant_normalization
    else:
        loss = -log_probs / nchars  * args.main_loss_multiplier

    expected_ntokens = None
    if args.length_penalty > 0.0:
        length_transition = (torch.ones(f_output_fwd_ids.size(-2)))[None, None, :, None].expand(
            *f_output_fwd_ids.size()).to(f_output_fwd_ms.device) * f_output_fwd_ms
        expected_lengths, _ = tokenizer.expectation(f_output_fwd_ts.reshape(-1, M, L),
                                                    length_transition.reshape(-1, M, L),
                                                    f_output_fwd_ms.reshape(batch_size * N, M, L),
                                                    f_output_lengths.reshape(
                                                        -1))  # lengths is reshaped to batch x N or batch-1 x N for skipgram
        expected_ntokens = expected_lengths.sum()
    # get regularizer necessary book-keeping
    if args.group_lasso > 0:
        assert not args.output_viterbi
        oent, _, om, _ = tokenizer(f_output_fwd_ids, f_output_fwd_ms, f_output_lengths,
                                 f_output_bwd_ids, f_output_bwd_ms, f_output_bwd_lengths,
                                 mmask, emask, None, lm=True, lm_mask=global_mask, fwd_ts=None if args.group_lasso_on_input else f_output_fwd_ts ,
                                 bwd_ts=None if args.group_lasso_on_input else f_output_bwd_ts)
        om_list = om.reshape(batch_size, -1)[input_mask[:, :om.size(1) * om.size(2)].to(torch.bool)]
        unit_list = input_ids[:, :om.size(1) * om.size(2)][input_mask[:, :om.size(1) * om.size(2)].to(torch.bool)].reshape(-1)
        if om_list.size() != unit_list.size():
            print("Bug Detected! Unmatched om_list and unit_list size!")
            code.interact(local=locals())
    else:
        om_list = unit_list = None


    if loss.isnan().any():
        code.interact(local=locals())

    if DEBUG:
        code.interact(local=locals())
    return None, loss, ent, f_output_lengths, ntokens, om_list, unit_list, expected_ntokens, None

def language_modeling_unigram_step(args, batch, tokenizer, model, device, skip_gram=False):
    # skip gram is handled in preprocessing so that this function applies exactly the same to skipgram and normal LMing
    batch = [t.to(device) if isinstance(t, torch.Tensor) else t for t in batch]
    input_ids, pos_ids, input_mask, labels, lengths, ntokens, text = batch

    # run model
    if args.debug_node_unigram:
        tril = torch.tril(torch.ones((input_ids.size(-1)//2, input_ids.size(-1)//2), dtype=torch.float, device=device))
        eye = torch.eye(input_ids.size(-1)//2, dtype=torch.float, device=device)
        left = torch.cat([tril, tril], dim=0)
        right = torch.cat([eye * 0, eye], dim=0)
        causal_mask = torch.cat([left, right], dim=1)
        causal_mask = causal_mask[None, ...].expand(input_ids.size(0), input_ids.size(1), input_ids.size(1))
    else:
        causal_mask = torch.tril(torch.ones((input_ids.size(-1), input_ids.size(-1)), dtype=torch.float, device=device))[None, ...].expand(input_ids.size(0), input_ids.size(1), input_ids.size(1))
    attn_bias = causal_mask * 0 + (1-causal_mask) * -INF
    losses = model(input_ids=input_ids, position_ids=pos_ids, attention_mask=input_mask, labels=labels, attn_bias=attn_bias, return_dict=True)

    # get loss
    loss = losses[0] * args.main_loss_multiplier
    return None, loss, None, lengths, ntokens, None, None, None, None # sum of mask is the number of tokens
