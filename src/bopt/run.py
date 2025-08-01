import glob
import math
import os
import sys
from collections import OrderedDict, defaultdict
from time import time
import cProfile

from torch.optim import Adam
from torch.optim.lr_scheduler import ReduceLROnPlateau
from torch.utils.data import RandomSampler, DataLoader, SequentialSampler
from torch.utils.data.dataloader import default_collate
from tqdm import tqdm

from bopt.core.utils import length_normalized_initialization
from bopt.data.language_modeling.unigram import preprocess_language_modeling_with_unigram_dataset, \
    LanguageModelingUnigramDataset, tokenize_language_modeling_with_unigram_dataset, \
    preprocess_language_modeling_with_unigram_node_dataset
from bopt.data.morpheme_prediction.unigram import preprocess_morpheme_prediction_with_unigram_dataset, MorphemePredictionUnigramDataset
from bopt.data.sentiment_analysis.lattice import preprocess_sentiment_analysis_with_lattices_dataset, \
    SentimentAnalysisLatticeDataset
from bopt.data.sentiment_analysis.unigram import preprocess_sentiment_analysis_with_unigram_dataset, \
    SentimentAnalysisUnigramDataset
from bopt.data.skip_gram.lattice import preprocess_skip_gram_with_lattices_dataset, SkipGramLatticeDataset
from bopt.data.skip_gram.unigram import SkipGramUnigramDataset, preprocess_skip_gram_with_unigram_dataset
from bopt.forward_step import morpheme_prediction_lattice_step, language_modeling_lattice_step, \
    language_modeling_unigram_step, morpheme_prediction_unigram_step
from bopt.forward_loop import language_modeling_lattice_loop, language_modeling_unigram_loop, \
    language_modeling_lattice_decode_loop, language_modeling_unigram_decode_loop, morpheme_prediction_lattice_loop, morpheme_prediction_unigram_loop

from bopt.arguments import parse_args
from bopt.core.tokenizer import Tokenizer
from bopt.data.morpheme_prediction.lattice import preprocess_morpheme_prediction_with_lattices_dataset, \
    MorphemePredictionLatticeDataset
from bopt.data.language_modeling.lattice import LanguageModelingLatticeDataset, \
    preprocess_language_modeling_with_lattices_dataset, preprocess_language_modeling_with_viterbi_lattices_dataset, \
    preprocess_language_modeling_with_lattices_output_viterbi_dataset, LanguageModelingLatticeOutputViterbiDataset
from grid_utils import acquire_all_available_gpu
import logging
import torch
import random
import numpy as np
import code
from bopt.core.modeling_bert import BertForMaskedLM, BertConfig
from bopt.data.utils import load_vocab, load_weights, constant_initializer, save_weights
import json

DEBUG = False
INF = 1e9

logging.basicConfig(format = '%(asctime)s - %(levelname)s - %(name)s -   %(message)s',
                    datefmt = '%m/%d/%Y %H:%M:%S',
                    stream=sys.stderr,
                    level = logging.INFO)
logger = logging.getLogger(__name__)

acquire_all_available_gpu()

# torch.set_anomaly_enabled(True)

DEBUG = False

def default_collate_(batch):
    for i in range(len(batch[0])):
        for j in [27]:
            print(i, len(batch[j][i]), batch[j][i])
    return default_collate(batch)

def initialize(args):
    # seed the experiment
    random.seed(args.seed)
    np.random.seed(args.seed)
    torch.manual_seed(args.seed)
    torch.cuda.manual_seed_all(args.seed)

    # get device info
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    n_gpu = torch.cuda.device_count()
    logger.info("device: {}, n_gpu {}".format(device, n_gpu))
    return device, n_gpu

def load_vocab_and_weights(args):
    logger.info("Loading vocabs and weight...")
    input_vocab = output_vocab = load_vocab(args.input_vocab)
    if args.output_vocab:
        output_vocab = load_vocab(args.output_vocab)
    if args.weights_file:
        weights = load_weights(args.weights_file)
    else:
        weights = constant_initializer(input_vocab, constant=0.0, mixture_count=args.mixture_count)
    return input_vocab, output_vocab, weights

def load_model(args, device):
    logger.info("Loading model...")
    if args.model_name is not None:
        config = BertConfig.from_json_file(os.path.join(args.model_name, "config.json"))
        model = BertForMaskedLM.from_pretrained(args.model_name)
    else:
        config = BertConfig.from_json_file(args.config)
        model = BertForMaskedLM(config)
    model.to(device)
    model.bias_mode = args.bias_mode if args.vopt else "mult_then_renorm" # mult_then_renorm is for attention causal masking
    if args.no_pos:
        model.bert.embeddings.no_pos = True
    return model, config


def load_tokenizer(args, input_vocab, weights, device):
    logger.info("Building tokenizer...")
    tokenizer = Tokenizer(vocab=input_vocab,
                          weights=weights,
                          log_space_parametrization=args.log_space,
                          continuing_subword_prefix=args.continuing_subword_prefix,
                          pad_token="[PAD]",
                          max_unit_length=args.max_unit_length,
                          specials=args.specials,
                          mixture_count=args.mixture_count
                          )
    args.max_unit_length = tokenizer.max_unit_length
    if args.vopt:
        tokenizer.to(device)
    return tokenizer

def create_or_clear_cache(args, cache_dir):
    if os.path.exists(cache_dir):
        if not args.overwrite_cache:
            logger.info(f"{cache_dir} exists, using it as is...")
            return False
        else:
            for f in glob.glob(f'{cache_dir}/*'):
                os.remove(f)
            return True
    else:
        os.makedirs(cache_dir)
        return True

def preprocess_datasets(args, tokenizer, input_vocab, output_vocab):
    logger.info("Preprocessing Datasets...")
    if args.task == "morpheme_prediction":
        datasets = {}
        dataloaders = {}
        for name, data in zip(["train", "eval", "test"], [args.train_dataset, args.eval_dataset,  args.test_dataset if args.test_dataset else args.eval_dataset]):
            if data is None:
                logger.info(f"No {name} dataset specified, continuing...")
                continue
            cache_dir = os.path.join(args.output_dir, f"cache", os.path.basename(data))
            flag = create_or_clear_cache(args, cache_dir)
            if flag:
                if args.vopt:
                    preprocess_morpheme_prediction_with_lattices_dataset(data,
                                                                         cache_dir,
                                                                         tokenizer,
                                                                         output_vocab,
                                                                         args.max_blocks,
                                                                         args.max_block_length,
                                                                         args.max_unit_length,
                                                                         debug=False)
                else:
                    preprocess_morpheme_prediction_with_unigram_dataset(args, data,
                                                                         cache_dir,
                                                                         tokenizer,
                                                                         output_vocab,
                                                                         args.max_blocks,
                                                                         args.max_block_length,
                                                                         args.max_unit_length,
                                                                         args.max_length,
                                                                         debug=False)
            if args.vopt:
                datasets[name] = dataset = MorphemePredictionLatticeDataset(cache_dir)
            else:
                datasets[name] = dataset = MorphemePredictionUnigramDataset(cache_dir)
            sampler = RandomSampler(dataset) if name == "train" else SequentialSampler(dataset)
            dataloaders[name] = dataloader = DataLoader(dataset, sampler=sampler, batch_size=args.gpu_batch_size, num_workers=args.data_num_workers)
    elif args.task == "sentiment_analysis":
        datasets = {}
        dataloaders = {}
        for name, data in zip(["train", "eval", "test"], [args.train_dataset, args.eval_dataset,  args.test_dataset if args.test_dataset else args.eval_dataset]):
            if data is None:
                logger.info(f"No {name} dataset specified, continuing...")
                continue
            cache_dir = os.path.join(args.output_dir, f"cache", os.path.basename(data))
            flag = create_or_clear_cache(args, cache_dir)
            if flag:
                if args.vopt:
                    preprocess_sentiment_analysis_with_lattices_dataset(args, data,
                                                                         cache_dir,
                                                                         tokenizer,
                                                                         output_vocab,
                                                                         args.max_blocks,
                                                                         args.max_block_length,
                                                                         args.max_unit_length,
                                                                         debug=False)
                else:
                    preprocess_sentiment_analysis_with_unigram_dataset(args, data,
                                                                         cache_dir,
                                                                         tokenizer,
                                                                         output_vocab,
                                                                         args.max_blocks,
                                                                         args.max_block_length,
                                                                         args.max_unit_length,
                                                                         args.max_length,
                                                                         debug=False)
            if args.vopt:
                datasets[name] = dataset = SentimentAnalysisLatticeDataset(cache_dir)
            else:
                datasets[name] = dataset = SentimentAnalysisUnigramDataset(cache_dir)
            sampler = RandomSampler(dataset) if name == "train" else SequentialSampler(dataset)
            dataloaders[name] = dataloader = DataLoader(dataset, sampler=sampler, batch_size=args.gpu_batch_size, num_workers=args.data_num_workers)
    elif args.task == "skip_gram":
        datasets = {}
        dataloaders = {}
        for name, data in zip(["train", "eval", "test"], [args.train_dataset, args.eval_dataset,  args.test_dataset if args.test_dataset else args.eval_dataset]):
            if data is None:
                logger.info(f"No {name} dataset specified, continuing...")
                continue
            cache_dir = os.path.join(args.output_dir, f"cache", os.path.basename(data))
            flag = create_or_clear_cache(args, cache_dir)
            if flag:
                if args.vopt:
                    preprocess_skip_gram_with_lattices_dataset(args,
                                                               data,
                                                               cache_dir,
                                                               tokenizer,
                                                               output_vocab,
                                                               args.max_blocks if name == "train" or args.eval_max_blocks is None else args.eval_max_blocks,
                                                               args.max_block_length if name == "train" or args.eval_max_block_length is None else args.eval_max_block_length,
                                                               args.max_unit_length if name == "train" or args.eval_max_unit_length is None else args.eval_max_unit_length)
                else:
                    preprocess_skip_gram_with_unigram_dataset(args,
                                                                      data,
                                                                      cache_dir,
                                                                      tokenizer,
                                                                      output_vocab,
                                                                      args.max_blocks if name == "train" or args.eval_max_blocks is None else args.eval_max_blocks,
                                                                      args.max_block_length if name == "train" or args.eval_max_block_length is None else args.eval_max_block_length,
                                                                      args.max_unit_length if name == "train" or args.eval_max_unit_length is None else args.eval_max_unit_length,
                                                                      args.max_length if name == "train" or args.eval_max_length is None else args.eval_max_length)
            if args.vopt:
                datasets[name] = dataset = SkipGramLatticeDataset(cache_dir, args.max_block_length if name == "train" or args.eval_max_block_length is None else args.eval_max_block_length)
            else:
                datasets[name] = dataset = SkipGramUnigramDataset(cache_dir, args.max_length if name == "train" or args.eval_max_length is None else args.eval_max_length)
            sampler = RandomSampler(dataset) if name == "train" else SequentialSampler(dataset)
            dataloaders[name] = dataloader = DataLoader(dataset, sampler=sampler,
                                                        batch_size=args.gpu_batch_size if name == "train" or args.eval_gpu_batch_size is None else args.eval_gpu_batch_size,
                                                        num_workers=args.data_num_workers, collate_fn=default_collate)
    elif args.task == "language_modeling":
        datasets = {}
        dataloaders = {}
        for name, data in zip(["train", "eval", "test"], [args.train_dataset, args.eval_dataset,  args.test_dataset if args.test_dataset else args.eval_dataset]):
            if data is None:
                logger.info(f"No {name} dataset specified, continuing...")
                continue
            cache_dir = os.path.join(args.output_dir, f"cache", os.path.basename(data))
            flag = create_or_clear_cache(args, cache_dir)
            if flag:
                if args.vopt:
                    if args.debug_viterbi_lattice:
                        preprocess_language_modeling_with_viterbi_lattices_dataset(data,
                                                                           cache_dir,
                                                                           tokenizer,
                                                                           output_vocab,
                                                                           args.max_blocks if name == "train" or args.eval_max_blocks is None else args.eval_max_blocks,
                                                                           args.max_block_length if name == "train" or args.eval_max_block_length is None else args.eval_max_block_length,
                                                                           args.max_unit_length if name == "train" or args.eval_max_unit_length is None else args.eval_max_unit_length)
                    elif args.output_viterbi:
                        preprocess_language_modeling_with_lattices_output_viterbi_dataset(args, data,
                                                                           cache_dir,
                                                                           tokenizer,
                                                                           output_vocab,
                                                                           args.max_blocks if name == "train" or args.eval_max_blocks is None else args.eval_max_blocks,
                                                                           args.max_block_length if name == "train" or args.eval_max_block_length is None else args.eval_max_block_length,
                                                                           args.max_unit_length if name == "train" or args.eval_max_unit_length is None else args.eval_max_unit_length)
                    else:
                        preprocess_language_modeling_with_lattices_dataset(data,
                                                                           cache_dir,
                                                                           tokenizer,
                                                                           output_vocab,
                                                                           args.max_blocks if name == "train" or args.eval_max_blocks is None else args.eval_max_blocks,
                                                                           args.max_block_length if name == "train" or args.eval_max_block_length is None else args.eval_max_block_length,
                                                                           args.max_unit_length if name == "train" or args.eval_max_unit_length is None else args.eval_max_unit_length)
                else:
                    if args.debug_node_unigram:
                        preprocess_language_modeling_with_unigram_node_dataset(data,
                                                                      cache_dir,
                                                                      tokenizer,
                                                                      output_vocab,
                                                                      args.max_blocks if name == "train" or args.eval_max_blocks is None else args.eval_max_blocks,
                                                                      args.max_block_length if name == "train" or args.eval_max_block_length is None else args.eval_max_block_length,
                                                                      args.max_unit_length if name == "train" or args.eval_max_unit_length is None else args.eval_max_unit_length,
                                                                      args.max_length if name == "train" or args.eval_max_length is None else args.eval_max_length,
                                                                      pos_length=args.pos_length)
                    else:
                        preprocess_language_modeling_with_unigram_dataset(args,
                                                                      data,
                                                                      cache_dir,
                                                                      tokenizer,
                                                                      output_vocab,
                                                                      args.max_blocks if name == "train" or args.eval_max_blocks is None else args.eval_max_blocks,
                                                                      args.max_block_length if name == "train" or args.eval_max_block_length is None else args.eval_max_block_length,
                                                                      args.max_unit_length if name == "train" or args.eval_max_unit_length is None else args.eval_max_unit_length,
                                                                      args.max_length if name == "train" or args.eval_max_length is None else args.eval_max_length)
            if args.vopt:
                if args.output_viterbi:
                    datasets[name] = dataset = LanguageModelingLatticeOutputViterbiDataset(cache_dir)
                else:
                    datasets[name] = dataset = LanguageModelingLatticeDataset(cache_dir)
            else:
                datasets[name] = dataset = LanguageModelingUnigramDataset(cache_dir)
            sampler = RandomSampler(dataset) if name == "train" else SequentialSampler(dataset)
            dataloaders[name] = dataloader = DataLoader(dataset, sampler=sampler, batch_size=args.gpu_batch_size if name == "train" or args.eval_gpu_batch_size is None else args.eval_gpu_batch_size,
                                                        num_workers=args.data_num_workers, collate_fn=default_collate)
    else:
        raise ValueError(args.task)
    return datasets, dataloaders

def build_optimizers(args, tokenizer, model):
    no_decay = ['bias', 'LayerNorm.bias', 'LayerNorm.weight']
    model_params = list(model.named_parameters())
    optimizer_grouped_parameters = [
        {'params': [p for n, p in model_params if not any(nd in n for nd in no_decay)], 'weight_decay': 0.0}, # TODO: if we want weight decay
        {'params': [p for n, p in model_params if any(nd in n for nd in no_decay)], 'weight_decay': 0.0},
    ]
    if args.vopt:
        optimizer_grouped_parameters.append({'params': tokenizer.parameters(), 'weight_decay': 0.0, 'lr': args.weights_learning_rate})
    optimizer = Adam(optimizer_grouped_parameters, lr=args.learning_rate)
    warmup_scheduler = torch.optim.lr_scheduler.LinearLR(optimizer, start_factor=1 / args.warmup_epochs , end_factor=1, total_iters=args.warmup_epochs)
    plat_scheduler = ReduceLROnPlateau(optimizer,'min', factor=1/4 )
    return optimizer, plat_scheduler


class Regularizers:

    @classmethod
    def regulairzation(cls, args, tokenizer, model, lengths, entropic_weight, ent, out_marginals, out_units, log_marginal_counts, counts, prev_lmc, prev_c, lmc, expected_ntokens, device="cpu"):
        l1 = torch.zeros((1,), device=device)
        e = torch.zeros((1,), device=device)
        gl = torch.zeros((1,), device=device)
        lp = torch.zeros((1,), device=device)
        if args.l1 > 0:
            if not tokenizer.lsp:
                l1 = args.l1 * tokenizer.weights.weight.mean()
            else:
                l1 = args.l1 * tokenizer.weights.weight.exp().mean()
        if args.vopt and entropic_weight != 0:
            nchars = lengths.sum(-1)
            avg_ent = ent / nchars
            e = entropic_weight * avg_ent.mean()
        elif args.vopt:
            nchars = lengths.sum(-1)
            avg_ent = ent / nchars
            e = avg_ent.mean().detach()
        if args.group_lasso > 0:
            # if cls.log_marginal_counts is None:
            #     cls.log_marginal_counts = torch.ones((len(tokenizer.vocab),), dtype=torch.float)
            for om, ou in zip(out_marginals.detach().cpu(), out_units.detach().cpu()):
                log_marginal_counts[ou] = torch.logaddexp(log_marginal_counts[ou], 2 * om) # the 2 is for the squaring
                lmc[ou] = torch.logaddexp(lmc[ou], om)
                counts[ou] = counts[ou] + 1.0
            if prev_lmc is not None:
                try:
                    # d/d out_marginals (group lasso) = lambda * sqrt(group_size) / sqrt(sum of squared marginals) * 2 exp om * exp om
                    log_global_multiplier = (prev_c.sqrt().log() - log_marginal_counts/2).to(device)
                    log_individual_multiplier = log_global_multiplier[out_units] + 2 * out_marginals.detach()
                    gl = args.group_lasso * (log_individual_multiplier.exp().to(device) * out_marginals).sum()
                except Exception as ex:
                    code.interact(local=locals())
        if args.length_penalty > 0:
            lp =  args.length_penalty * expected_ntokens / lengths.size(0) # normalize by batchsize
        return l1, e, gl, lp

def save_checkpoint(args, epoch, step, model, tokenizer, optimizer):
    checkpointdir = os.path.join(args.output_dir, f"checkpoint-{step}")
    os.makedirs(checkpointdir, exist_ok=True)

    # save_vocab
    output_vocab_file = os.path.join(checkpointdir, "learned_vocab.txt")
    weights = tokenizer.weights.weight.detach().tolist() if tokenizer.lsp else tokenizer.weights.weight.log().detach().tolist()
    save_weights(OrderedDict(zip(tokenizer.vocab, weights)), output_vocab_file)

    if args.only_save_vocab:
        return
    # Save a trained model
    # Save a trained model, configuration and tokenizer
    model_to_save = model.module if hasattr(model, 'module') else model  # Only save the model it-self

    # If we save using the predefined names, we can load using `from_pretrained`
    output_model_file = os.path.join(checkpointdir, "pytorch_model.bin")
    output_config_file = os.path.join(checkpointdir, "config.json")

    # save model and config
    torch.save(model_to_save.state_dict(), output_model_file)
    model_to_save.config.to_json_file(output_config_file)

    # save_optimizer
    output_optim_file = os.path.join(checkpointdir, "optim.bin")
    torch.save({"optimizer_state_dict": optimizer.state_dict()}, output_optim_file)


def log_predictions(file, predictions):
    with open(file, "wt") as f:
        for prediction in predictions:
            print(prediction, file=f)

def train(args, model: BertForMaskedLM, tokenizer:Tokenizer, train_dataloader: DataLoader,eval_dataloader: DataLoader, test_dataloader: DataLoader, optimizer, lr_scheduler, device="cpu"):
    logger.info("Training...")
    model.train()
    bn = 0
    step = 0
    entropic_weight = 0
    prev_counts = prev_log_marginal_counts = prev_lmc = prev_type_ent = None
    out_vocab_count = model.cls.predictions.bias.numel()
    expert_padding = torch.tensor([-INF] * (out_vocab_count - len(tokenizer.vocab)),device=device)
    unigram_expert = None if not args.unigram_expert else torch.cat([torch.log_softmax(tokenizer.weights.weight.reshape(-1), dim=-1), expert_padding], dim=-1)
    if args.fixed_unigram_expert:
        unigram_expert = unigram_expert.detach()
    for epoch in range(args.train_epochs):
        if args.entropic != 0:
            if epoch < args.entropy_start_dec:
                entropic_weight = args.entropic * max(0, min(1, (epoch - args.entropy_start) / (args.entropy_end - args.entropy_start)))
            else:
                entropic_weight = args.entropic * min(1, max(0,  1 - (epoch - args.entropy_start_dec) / (args.entropy_end_dec - args.entropy_start_dec)))
        weight = args.gpu_batch_size / args.train_batch_size # TODO: if gpu_batch_size approaches the size of the dataset, make sure to drop_last
        epoch_loss = epoch_l1 = epoch_e =  epoch_examples = epoch_gl = epoch_lp = 0
        tqdm_bar = tqdm(train_dataloader, total=len(train_dataloader) * args.train_epochs, initial=epoch * len(train_dataloader))
        log_marginal_counts = torch.ones((len(tokenizer.vocab),), dtype=torch.float) * -INF
        lmc = torch.ones((len(tokenizer.vocab),), dtype=torch.float) * -INF
        counts = torch.zeros((len(tokenizer.vocab),), dtype=torch.float)
        for batch in tqdm_bar:
            if (bn + 1) % (args.train_batch_size // args.gpu_batch_size) == 0 or bn == 0:
                if (step % args.eval_steps) == 0 or bn == 0:
                    model.eval()
                    if args.task == "morpheme_prediction" or args.task == "sentiment_analysis":
                        if args.vopt:
                            eval_loss_log, eval_loss_zero_one, eval_loss_expected_zero_one, eval_example_total, eval_num_predictions, eval_tok_precision, eval_tok_recall, eval_tok_f1, eval_path_marginal, eval_tok_marginal, eval_astat, eval_predictions = morpheme_prediction_lattice_loop(
                                args, eval_dataloader, tokenizer, model, device, not_morpheme=args.task != "morpheme_prediction")
                            if args.task == "morpheme_prediction":
                                logger.info(
                                    f"Eval loss at step {step}: loss = {eval_loss_log}, 0/1: {eval_loss_zero_one:.2f}, E[0/1]: {eval_loss_expected_zero_one:.2f}, ex = {eval_example_total}, pred = {eval_num_predictions}, "
                                    f"tprec={eval_tok_precision:.2f}, trec={eval_tok_recall:.2f}, tf1={eval_tok_f1:.2f}, pm={eval_path_marginal:.2f}, tm={eval_tok_marginal:.2f}, leakage={eval_astat['leakage']} / {eval_astat['total_attention_dist_count']}={eval_astat['leakage'] / eval_astat['total_attention_dist_count']:.2f}, over={eval_astat['over_attention_mean']} * {eval_astat['over_attention_count']} "
                                    f"({eval_astat['over_attention_mass']:.2f} / {eval_astat['total_attention_dist_count']} = {eval_astat['over_attention_mass'] / eval_astat['total_attention_dist_count']:.2f} mass), a-ent={eval_astat['entropy_mean']:.2f}, ({eval_astat['entropy_std']:.2f})")
                            else:
                                logger.info(
                                    f"Eval loss at step {step}: loss = {eval_loss_log}, 0/1: {eval_loss_zero_one:.2f}, E[0/1]: {eval_loss_expected_zero_one:.2f}, ex = {eval_example_total}, pred = {eval_num_predictions}, ")

                        else:
                            eval_loss_log, eval_loss_zero_one, eval_loss_expected_zero_one, eval_example_total, eval_num_predictions, eval_tok_precision, eval_tok_recall, eval_tok_f1, eval_path_marginal, eval_tok_marginal, eval_astat, eval_predictions = morpheme_prediction_unigram_loop(
                                args, eval_dataloader, tokenizer, model, device)
                            logger.info(
                                f"Eval loss at step {step}: loss = {eval_loss_log}, 0/1: {eval_loss_zero_one:.2f}, E[0/1]: {eval_loss_expected_zero_one:.2f}, ex = {eval_example_total}, pred = {eval_num_predictions}, ")

                        log_predictions(os.path.join(args.output_dir, f"test_predictions_{step}.txt"), eval_predictions)
                        if args.vopt:
                            test_loss_log, test_loss_zero_one, test_loss_expected_zero_one, test_example_total, test_num_predictions, test_tok_precision, test_tok_recall, test_tok_f1, test_path_marginal, test_tok_marginal, test_astat, test_predictions = morpheme_prediction_lattice_loop(
                                args, test_dataloader, tokenizer, model, device, not_morpheme=args.task != "morpheme_prediction")
                            if args.task == "morpheme_prediction":
                                logger.info(
                                    f"Test loss at step {step}: loss = {test_loss_log}, 0/1: {test_loss_zero_one:.2f}, E[0/1]: {test_loss_expected_zero_one:.2f}, ex = {test_example_total}, pred = {test_num_predictions}, "
                                    f"tprec={test_tok_precision:.2f}, trec={test_tok_recall:.2f}, tf1={test_tok_f1:.2f}, pm={test_path_marginal:.2f}, tm={test_tok_marginal:.2f}, leakage={test_astat['leakage']} / {test_astat['total_attention_dist_count']}={test_astat['leakage'] / test_astat['total_attention_dist_count']:.2f}, over={test_astat['over_attention_mean']} * {test_astat['over_attention_count']}"
                                    f"({test_astat['over_attention_mass']:.2f} / {test_astat['total_attention_dist_count']} = {test_astat['over_attention_mass'] / test_astat['total_attention_dist_count']:.2f} mass), a-ent={test_astat['entropy_mean']:.2f}, ({test_astat['entropy_std']:.2f})")
                            else:
                                logger.info(
                                f"Test loss at step {step}: loss = {test_loss_log}, 0/1: {test_loss_zero_one:.2f}, E[0/1]: {test_loss_expected_zero_one:.2f}, ex = {test_example_total}, pred = {test_num_predictions}, ")

                        else:
                            test_loss_log, test_loss_zero_one, test_loss_expected_zero_one, test_example_total, test_num_predictions, test_tok_precision, test_tok_recall, test_tok_f1, test_path_marginal, test_tok_marginal, test_astat, test_predictions = morpheme_prediction_unigram_loop(
                                args, test_dataloader, tokenizer, model, device)
                            logger.info(
                                f"Test loss at step {step}: loss = {test_loss_log}, 0/1: {test_loss_zero_one:.2f}, E[0/1]: {test_loss_expected_zero_one:.2f}, ex = {test_example_total}, pred = {test_num_predictions}, ")
                        log_predictions(os.path.join(args.output_dir, f"test_predictions_{step}.txt"), test_predictions)

                        with open(os.path.join(args.output_dir, "log.json"), "a") as f:
                            print(json.dumps({
                                "step": step,
                                "eval_log_loss": eval_loss_log,
                                "eval_zero_one_loss": eval_loss_zero_one,
                                "eval_expected_zero_one_loss": eval_loss_expected_zero_one,
                                "eval_n_example": eval_example_total,
                                "eval_n_prediction": eval_num_predictions,
                                "test_log_loss": test_loss_log,
                                "test_zero_one_loss": test_loss_zero_one,
                                "test_expected_zero_one_loss": test_loss_expected_zero_one,
                                "test_n_example": test_example_total,
                                "test_n_prediction": test_num_predictions,
                                "train_loss": epoch_loss / epoch_examples if epoch_examples > 0 else 0,
                                "train_ent": epoch_e / epoch_examples if epoch_examples > 0 else 0,
                                "train_l1": epoch_l1 / epoch_examples if epoch_examples > 0 else 0,
                                "train_lp": epoch_lp / epoch_examples if epoch_examples > 0 else 0,
                                "eval_tok_prec": eval_tok_precision,
                                "eval_tok_recall": eval_tok_recall,
                                "eval_tok_f1": eval_tok_f1,
                                "eval_tok_marginal": eval_tok_marginal,
                                "eval_path_marginal": eval_path_marginal,
                                "test_tok_prec": test_tok_precision,
                                "test_tok_recall": test_tok_recall,
                                "test_tok_f1": test_tok_f1,
                                "test_tok_marginal": test_tok_marginal,
                                "test_path_marginal": test_path_marginal,
                                "eval_leakage": eval_astat["leakage"] if eval_astat else None,
                                "eval_over_attention_mean": eval_astat["over_attention_mean"] if eval_astat else None,
                                "eval_over_attention_count": eval_astat["over_attention_count"] if eval_astat else None,
                                "eval_over_attention_mass": eval_astat["over_attention_mass"] if eval_astat else None,
                                "eval_total_attention_count": eval_astat["total_attention_count"] if eval_astat else None,
                                "eval_total_attention_dist_count": eval_astat["total_attention_dist_count"] if eval_astat else None,
                                "eval_entropy_mean": eval_astat["entropy_mean"] if eval_astat else None,
                                "eval_entropy_std": eval_astat["entropy_std"] if eval_astat else None,
                                "test_leakage": test_astat["leakage"] if test_astat else None,
                                "test_over_attention_mean": test_astat["over_attention_mean"] if test_astat else None,
                                "test_over_attention_count": test_astat["over_attention_count"] if test_astat else None,
                                "test_over_attention_mass": test_astat["over_attention_mass"] if test_astat else None,
                                "test_total_attention_count": test_astat["total_attention_count"] if test_astat else None,
                                "test_total_attention_dist_count": test_astat["total_attention_dist_count"] if test_astat else None,
                                "test_entropy_mean": test_astat["entropy_mean"] if test_astat else None,
                                "test_entropy_std": test_astat["entropy_std"] if test_astat else None,
                            }), file=f)
                    elif args.task == "language_modeling" or args.task == "skip_gram":
                        if args.vopt:
                            eval_loss_avg_c, eval_loss_avg_t, eval_loss, eval_NC, eval_NT = language_modeling_lattice_loop(
                                args, eval_dataloader, tokenizer, model, device, unigram_expert=unigram_expert,
                                skip_gram=args.task == "skip_gram")
                        else:
                            eval_loss_avg_c, eval_loss_avg_t, eval_loss, eval_NC, eval_NT = language_modeling_unigram_loop(
                                args, eval_dataloader, tokenizer, model, device, skip_gram=args.task == "skip_gram")
                        logger.info(
                            f"Eval loss at step {step}: avgc = {eval_loss_avg_c}, avgt = {eval_loss_avg_t}, loss = {eval_loss}, NC = {eval_NC}, NT = {eval_NT}, "
                            f"EPC = {model.cls.predictions.expert_coefficient.item() if args.unigram_expert else -42.0}")
                        if args.vopt:
                            test_loss_avg_c, test_loss_avg_t, test_loss, test_NC, test_NT = language_modeling_lattice_loop(
                                args, test_dataloader, tokenizer, model, device, unigram_expert=unigram_expert,
                                skip_gram=args.task == "skip_gram")
                        else:
                            test_loss_avg_c, test_loss_avg_t, test_loss, test_NC, test_NT = language_modeling_unigram_loop(
                                args, test_dataloader, tokenizer, model, device, skip_gram=args.task == "skip_gram")
                        logger.info(
                            f"Test loss at step {step}: avgc = {test_loss_avg_c}, avgt = {test_loss_avg_t}, loss = {test_loss}, NC = {test_NC}, NT = {test_NT}, "
                            f"EPC = {model.cls.predictions.expert_coefficient.item() if args.unigram_expert else -42.0}")

                        with open(os.path.join(args.output_dir, "log.json"), "a") as f:
                            print(json.dumps({
                                "step": step,
                                "eval_avg_char": eval_loss_avg_c,
                                "eval_avg_token": eval_loss_avg_t,
                                "eval_loss": eval_loss,
                                "eval_n_char": eval_NC,
                                "eval_n_token": eval_NT,
                                "test_avg_char": test_loss_avg_c,
                                "test_avg_token": test_loss_avg_t,
                                "test_loss": test_loss,
                                "test_n_char": test_NC,
                                "test_n_token": test_NT,
                                "train_loss": epoch_loss / epoch_examples if epoch_examples > 0 else 0,
                                "train_ent": epoch_e / epoch_examples if epoch_examples > 0 else 0,
                                "train_l1": epoch_l1 / epoch_examples if epoch_examples > 0 else 0,
                                "train_lp": epoch_lp / epoch_examples if epoch_examples > 0 else 0,
                                "group_lasso": epoch_gl / epoch_examples if epoch_examples > 0 else 0,
                                "type_entropy": -42.0 if prev_type_ent is None else prev_type_ent.item(),
                                "expert_coefficient": model.cls.predictions.expert_coefficient.item() if args.unigram_expert else -42.0
                            }), file=f)
                    else:
                        raise ValueError
                    model.train()

            # if not batch[-2][0].startswith("some changes to the plan"):
            #     continue

            bn += 1
            # load inputs
            batch_size = batch[0].size(0)

            if args.task == "morpheme_prediction" or args.task == "sentiment_analysis":
                if args.vopt:
                    _, loss, ent, lengths, ntokens, out_marginals, out_units, expected_ntokens, _ = morpheme_prediction_lattice_step(args, batch, tokenizer, model, device)
                else:
                    _, loss, ent, lengths, ntokens, out_marginals, out_units, expected_ntokens, _ = morpheme_prediction_unigram_step(args, batch, tokenizer, model, device)
            elif args.task == "language_modeling":
                if args.vopt:
                    _, loss, ent, lengths, ntokens, out_marginals, out_units, expected_ntokens, _ = language_modeling_lattice_step(args, batch, tokenizer, model, device, unigram_expert=unigram_expert)
                else:
                    _, loss, ent, lengths, ntokens, out_marginals, out_units, expected_ntokens, _ = language_modeling_unigram_step(args, batch, tokenizer, model, device)
            elif args.task == "skip_gram":
                if args.vopt:
                    _, loss, ent, lengths, ntokens, out_marginals, out_units, expected_ntokens, _ = language_modeling_lattice_step(args, batch, tokenizer, model, device, unigram_expert=unigram_expert, skip_gram=True)
                else:
                    _, loss, ent, lengths, ntokens, out_marginals, out_units, expected_ntokens, _ = language_modeling_unigram_step(args, batch, tokenizer, model, device)
            else:
                raise ValueError

            # get regularizations
            l1, e, gl, lp = Regularizers.regulairzation(args, tokenizer, model, lengths, entropic_weight, ent, out_marginals, out_units, log_marginal_counts, counts, prev_log_marginal_counts, prev_counts, lmc, expected_ntokens, device=device)

            # weight the loss and backpropograte
            Li = weight * (loss + l1 + e + gl + lp)
            Li.backward()
            # code.interact(local=locals())
            # for group in optimizer.param_groups:
            #     for param in group["params"]:
            #         if param.grad.isnan().any():
            #             print("Nan gradient")
            #             code.interact(local=locals())

            # bookkeep
            epoch_examples += batch_size
            epoch_loss += loss.item() * batch_size

            epoch_l1 += l1.item() * batch_size
            epoch_gl += gl.item() * batch_size
            epoch_e += e.item() * batch_size
            epoch_lp += lp.item() * batch_size
            tqdm_bar.desc = f"Epoch {epoch:<4} " \
                            f"Step {step:<4} " \
                            f"Task {epoch_loss / epoch_examples:<4.2f} " \
                            f"L1 {epoch_l1 / epoch_examples:<6.4f} " \
                            f"GL {epoch_gl / epoch_examples:<6.4f} " \
                            f"Ent {epoch_e / epoch_examples:<6.4f} " \
                            f"TEnt {-42.0 if prev_type_ent is None else prev_type_ent.item():<6.4f} " \
                            f"GnormM {(sum([(param.grad ** 2).sum() for param in list(model.parameters()) if param.grad is not None], torch.tensor(0, device=device))**0.5).item():<6.4f} " \
                            f"GnormV {(sum([(param.grad ** 2).sum() for param in list(tokenizer.parameters()) if param.grad is not None], torch.tensor(0, device=device)) ** 0.5).item():<10.8f} " \
                            f"LR " + " ".join([f"{param_group['lr']:<6.4f}" for param_group in optimizer.param_groups]) + " " \
                            f"EPC = {model.cls.predictions.expert_coefficient.item() if args.unigram_expert else -42.0}" \
                            f"LP = {epoch_lp / epoch_examples:<6.4f}"
            # step
            if (bn + 1) % ( args.train_batch_size // args.gpu_batch_size) == 0:
                # clip grad
                for group in optimizer.param_groups:
                    torch.nn.utils.clip_grad_norm_((param for param in group['params']), args.max_grad_norm)
                optimizer.step()
                # sweight = tokenizer.get_singleton_weight()
                optimizer.zero_grad(set_to_none=False)
                # tokenizer.set_singleton_weight(sweight)
                tokenizer.reset_padding_weight()
                tokenizer.reset_specials_weight()
                if not tokenizer.lsp:
                    # make sure weights are positive if parametrized as real numbers
                    tokenizer.clamp_weights()
                if not args.log_space and (tokenizer.weights.weight.data <= -1e-6).any() or args.log_space and (tokenizer.weights.weight.data <= -14).any():
                    code.interact(local=locals())
                step += 1
                if DEBUG:
                    code.interact(local=locals())
                # evaluate
                if (step % args.save_steps) == 0:
                    save_checkpoint(args, epoch, step, model, tokenizer, optimizer)

                if args.unigram_expert and not args.fixed_unigram_expert:
                    unigram_expert = torch.cat([torch.log_softmax(tokenizer.weights.weight.reshape(-1), dim=-1), expert_padding], dim=-1)
        prev_log_marginal_counts = log_marginal_counts
        prev_counts = counts
        prev_lmc = lmc
        prev_type_ent = (-(prev_lmc - prev_lmc.logsumexp(-1)).to(torch.double) * (prev_lmc - prev_lmc.logsumexp(-1)).to(torch.double).exp()).sum()

        if (epoch + 1) % args.save_epochs == 0:
            save_checkpoint(args, epoch, step, model, tokenizer, optimizer)
        lr_scheduler.step(epoch_loss / epoch_examples)

def eval(args, model: BertForMaskedLM, tokenizer:Tokenizer, eval_dataloader: DataLoader, device="cpu"):
    if args.task == "morpheme_prediction" or args.task == "sentiment_analysis":
        pass
    elif args.task == "language_modeling":
        if args.vopt:
            eval_loss_avg_c, eval_loss_avg_t, eval_loss, eval_NC, eval_NT = language_modeling_lattice_loop(args, eval_dataloader, tokenizer, model, device)
            logger.info(f"Eval loss: avgc = {eval_loss_avg_c}, avgt = {eval_loss_avg_t}, loss = {eval_loss}, NC = {eval_NC}, NT = {eval_NT}")
        else:
            eval_loss_avg_c, eval_loss_avg_t, eval_loss, eval_NC, eval_NT = language_modeling_unigram_loop(args, eval_dataloader, tokenizer, model, device)
            logger.info(f"Eval loss: avgc = {eval_loss_avg_c}, avgt = {eval_loss_avg_t}, loss = {eval_loss}, NC = {eval_NC}, NT = {eval_NT}")


def decode(args, model, tokenizer, eval_dataloader, device="cpu"):
    if args.task == "morpheme_prediction" or args.task == "sentiment_analysis":
        pass
    elif args.task == "language_modeling":
        if args.vopt:
            decodings = language_modeling_lattice_decode_loop(args, eval_dataloader, tokenizer, model, device, remove_csp=args.decode_remove_csp, remove_padding=args.decode_remove_padding)
        else:
            decodings = language_modeling_unigram_decode_loop(args, eval_dataloader, tokenizer, model, device, remove_csp=args.decode_remove_csp, remove_padding=args.decode_remove_padding)
        with open(os.path.join(args.output_dir, f"{os.path.basename(args.eval_dataset)}.viterbi.txt"), "wt") as f:
            for decoding in decodings:
                print(" ".join(decoding), file=f)
def tokenize(args, tokenizer):
    if args.task == "language_modeling":
        tokenize_language_modeling_with_unigram_dataset(args.eval_dataset, tokenizer)
    else:
        raise ValueError

def main():
    args = parse_args()

    # initialize experiment
    device, n_gpu = initialize(args)
    if args.double_precision:
        torch.set_default_dtype(torch.float64)

    # load labels, vocab, and weights from cmdline arguments
    input_vocab, output_vocab, weights = load_vocab_and_weights(args)

    # build tokenizer
    tokenizer = load_tokenizer(args, input_vocab, weights, device)

    if args.do_tokenize:
        logger.info("Tokenizing...")
        # toknenizing is a separate mode from training that only uses the tokenizer
        tokenize(args, tokenizer)
        return None

    # load model from path / config file
    model, config = load_model(args, device)
    if args.model_name is None and args.length_normalized_initialization:
        length_normalized_initialization(model, tokenizer)
    # do some logging of model size
    model_size = sum(parameter.numel() for parameter in model.parameters())
    tokenizer_size = sum(parameter.numel() for parameter in tokenizer.parameters())
    logger.info(
        f"Loaded transformer model with {model_size} parameters and vocab weight with {tokenizer_size} parameters, "
        f"percentage of weight among all parameters weights is {tokenizer_size / (tokenizer_size + model_size):e}")

    if args.do_train:
        if not args.quiet:
            input("Please hit enter if you want to overwrite the directory (esp. log.json)...")
        with open(os.path.join(args.output_dir, "log.json"), "wt") as f:
            pass

    # build datasets
    datasets, dataloaders = preprocess_datasets(args, tokenizer, input_vocab, output_vocab)

    # build optimizers
    optimizer, lr_scheduler = build_optimizers(args, tokenizer, model)

    # train!
    if args.do_train:
        logger.info("Training...")
        train(args, model, tokenizer, dataloaders["train"], dataloaders["eval"], dataloaders["test"], optimizer, lr_scheduler, device=device)
    #    code.interact(local=locals())
    if args.do_eval:
        logger.info("Evaluating...")
        model.eval()
        eval(args, model, tokenizer, dataloaders["eval"], device=device)
    #    code.interact(local=locals())
    if args.do_decode:
        logger.info("Decoding...")
        decode(args, model, tokenizer, dataloaders["eval"], device=device)
    if args.do_inspection:
        logger.info("Decoding...")
        code.interact(local=locals())


if __name__ == "__main__":
    torch.set_printoptions(profile="full", sci_mode=False, precision=2, linewidth=200)
    main()
    # cProfile.run("main()")
