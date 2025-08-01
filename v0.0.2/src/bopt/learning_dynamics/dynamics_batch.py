import code
from itertools import product

import torch

from bopt.learning_dynamics.saving import save_learning_dynamics_log



def dynamics_batch(setup, step, raw_step, ids, sentences, labels):
    loss = 0
    accumulation_factor = (setup.args.gpu_batch_size / setup.args.train_batch_size)
    output = setup.classifier(setup, ids, sentences, labels, output_attentions=True, output_inputs=True,  mode="train")

    # add to training loss
    loss += output.task_loss
    if setup.args.L1 > 0: loss += setup.args.L1 * output.regularizers.l1
    if setup.args.annealing > 0:
        loss += setup.annealing_scheduler(step) * output.regularizers.entropy
    loss = loss * accumulation_factor

    # dynamics logging start
    attentions = output.attentions # L instances of B x H x seq_len x seq_len
    attentions_grad = torch.autograd.grad(output.task_loss * accumulation_factor, attentions, retain_graph=True)
    attentions_std = torch.std(torch.cat(attentions, dim=1), dim=1) # B x seq_len x seq_len
    attentions = sum(attentions).sum(dim=1) # B x seq_len x seq_len
    attentions_grad_std = torch.std(torch.cat(attentions_grad, dim=1), dim=1) # B x seq_len x seq_len
    attentions_grad = sum(attentions_grad).sum(dim=1) # B x seq_len x seq_len
    conditional_marginals = output.attention_bias # B x seq_len x seq_len
    conditional_marginals_grad = torch.autograd.grad(output.task_loss * accumulation_factor, conditional_marginals,retain_graph=True)[0] # B x seq_len x seq_len
    weights_grad = torch.autograd.grad(output.task_loss * accumulation_factor, output.edge_log_potentials, retain_graph=True)[0] # B x N x M x L
    weights_grad_entropy = torch.autograd.grad(setup.annealing_scheduler(step) * output.regularizers.entropy * accumulation_factor, output.edge_log_potentials, retain_graph=True)[0] # B x N x M x L
    weights = setup.classifier.input_tokenizer.unigramlm.log_weights().reshape(-1)
    save_learning_dynamics_log(setup, step, raw_step,
                               input_ids=output.input_ids.detach().cpu(),
                               type_ids=output.type_ids.detach().cpu(),
                               position_ids=output.position_ids.detach().cpu(),
                               attention_mask=output.attention_mask.detach().cpu(),
                               forward_encodings=output.forward_encodings.detach().cpu(),
                               attentions=attentions.detach().cpu(),
                               attentions_std=attentions_std.detach().cpu(),
                               attentions_grad=attentions_grad.detach().cpu(),
                               attentions_grad_std=attentions_grad_std.detach().cpu(),
                               conditional_marginals=conditional_marginals.detach().cpu(),
                               conditional_marginals_grad=conditional_marginals_grad.detach().cpu(),
                               weights=weights.detach().cpu(),
                               weights_grad_task=weights_grad.detach().cpu(),
                               weights_grad_entropy=weights_grad_entropy.detach().cpu(),
                               weights_grad_l1=torch.zeros_like(weights).fill_(setup.args.L1).detach().cpu(),
                               ids=ids,
                               sentences=sentences,
                               labels=labels)
    return loss



# def dynamics_batch_full(setup, step, ids, sentences, labels,
#                    weights_dynamics,
#                    attention_dynamics,
#                    conditional_marginal_dynamics,
#                    weights_grad_dynamics,
#                    attention_grad_dynamics,
#                    conditional_marginal_grad_dynamics):
#     accumulation_factor = (setup.args.gpu_batch_size / setup.args.train_batch_size)
#     L = setup.classifier.model.config.num_hidden_layers
#     H = setup.classifier.model.config.num_attention_heads
#     pad_id = setup.classifier.model.config.pad_token_id
#     loss = 0
#     output = setup.classifier(setup, ids, sentences, labels, output_attentions=True, output_inputs=True,  mode="train")
#     # add to training loss
#     loss += output.task_loss
#     if setup.args.L1 > 0: loss += setup.args.L1 * output.regularizers.l1
#     if setup.args.annealing > 0:
#         loss += setup.annealing_scheduler(step) * output.regularizers.entropy
#     loss = loss * accumulation_factor
#     # dynamics logging start
#     attentions = output.attentions
#     attentions_grad = torch.autograd.grad(output.task_loss * accumulation_factor, attentions, retain_graph=True)
#     conditional_marginals = output.attention_bias
#     attentions_ref = [[attentions[ll][0, hh] for hh in range(H)] for ll in range(L)]
#     conditional_marginals_grad = [[torch.autograd.grad(attentions_ref[ll][hh], conditional_marginals,
#                                                        grad_outputs=attentions_grad[ll][0, hh],
#                                                        retain_graph=True)[0] for hh in range(H)] for ll in range(L)]
#     weights_grad = [[torch.autograd.grad(attentions_ref[ll][hh], output.edge_log_potentials,
#                                          grad_outputs=attentions_grad[ll][0, hh],
#                                          retain_graph=True)[0] for hh in range(H)] for ll in range(L)]
#     weights_grad_entropy = torch.autograd.grad(setup.annealing_scheduler(step) *
#                                                output.regularizers.entropy *
#                                                accumulation_factor,
#                                                output.edge_log_potentials, retain_graph=True)[0]
#     attention_dynamics[tuple()].append((ids, attentions))
#     attention_grad_dynamics[tuple()].append((ids,TASK_LOSS, attentions_grad))
#     conditional_marginal_dynamics[tuple()].append((ids,conditional_marginals))
#     conditional_marginal_grad_dynamics[tuple()].append((ids,TASK_LOSS, conditional_marginals_grad))
#     weights = setup.classifier.input_tokenizer.unigramlm.log_weights().reshape(-1)
#     weights_dynamics[tuple()] = (-1, weights)
#     weights_grad_dynamics[tuple()].append((-1, TASK_LOSS, weights_grad))
#     weights_grad_dynamics[tuple()].append((-1, ENTROPY, weights_grad_entropy))
#     weights_grad_dynamics[tuple()].append((-1, L1, torch.zeros_like(weights).fill_(setup.args.L1)))
#     return loss

# def dynamics_batch(setup, step, ids, sentences, labels,
#                    weights_dynamics,
#                    attention_dynamics,
#                    conditional_marginal_dynamics,
#                    weights_grad_dynamics,
#                    attention_grad_dynamics,
#                    conditional_marginal_grad_dynamics):
#     accumulation_factor = (setup.args.gpu_batch_size / setup.args.train_batch_size)
#     L = setup.classifier.model.config.num_hidden_layers
#     H = setup.classifier.model.config.num_attention_heads
#     pad_id = setup.classifier.model.config.pad_token_id
#     loss = 0
#     for id, sentence, label in zip(ids, sentences, labels):
#         output = setup.classifier(setup, [id], [sentence], [label], output_attentions=True, output_inputs=True,  mode="train")
#         # add to training loss
#         loss += output.task_loss * accumulation_factor
#         if setup.args.L1 > 0: loss += setup.args.L1 * output.regularizers.l1 / len(ids) * accumulation_factor
#         if setup.args.annealing > 0:
#             loss += setup.annealing_scheduler(step) * output.regularizers.entropy * accumulation_factor
#         # dynamics logging start
#         input_ids = output.input_ids.view(-1).tolist()
#         attentions = output.attentions
#         attentions_grad = torch.autograd.grad(output.task_loss * accumulation_factor, attentions, retain_graph=True)
#         conditional_marginals = output.attention_bias
#         attentions_ref = [[attentions[ll][0, hh] for hh in range(H)] for ll in range(L)]
#         conditional_marginals_grad = [[torch.autograd.grad(attentions_ref[ll][hh], conditional_marginals,
#                                                            grad_outputs=attentions_grad[ll][0, hh],
#                                                            retain_graph=True)[0] for hh in range(H)] for ll in range(L)]
#         weights_grad = [[torch.autograd.grad(attentions_ref[ll][hh], output.edge_log_potentials,
#                                              grad_outputs=attentions_grad[ll][0, hh],
#                                              retain_graph=True)[0] for hh in range(H)] for ll in range(L)]
#         weights_grad_entropy = torch.autograd.grad(setup.annealing_scheduler(step) *
#                                                    output.regularizers.entropy *
#                                                    accumulation_factor,
#                                                    output.edge_log_potentials, retain_graph=True)[0]
#
#         for weight_id, grad in enumerate(weights_grad_entropy.view(-1).tolist()):
#             weights_grad_dynamics[(weight_id,)].append((step, id, ENTROPY, grad))
#         for l in tqdm(range(L)):
#             for h in range(H):
#                 for n in range(output.forward_encodings.size(1)):
#                     for m in range(output.forward_encodings.size(2)):
#                         for bl in range(output.forward_encodings.size(3)):
#                             forward_id = output.forward_encodings[0, n, m, bl].item()
#                             if forward_id != PADEDGE_ID and forward_id != NONEDGE_ID:
#                                 weights_grad_dynamics[(l, h, forward_id)].append(
#                                     (step, id, TASK_LOSS, weights_grad[l][h][0, n, m, bl].item()))
#                 for (i, wi), (j, wj) in product(list(enumerate(input_ids)), list(enumerate(input_ids))):
#                     if wi == pad_id or wj == pad_id: continue
#                     attention_dynamics[(l, h, wi, wj)].append((step, id, attentions[l][0, h, i, j].item()))
#                     conditional_marginal_dynamics[(wi, wj)].append((step, id, conditional_marginals[0, i, j].item()))
#                     attention_grad_dynamics[(l, h, wi, wj)].append(
#                         (step, id, TASK_LOSS, attentions_grad[l][0, h, i, j].item()))
#                     conditional_marginal_grad_dynamics[(l, h, wi, wj)].append(
#                         (step, id, TASK_LOSS, conditional_marginals_grad[l][h][0, i, j].item()))
#     return loss