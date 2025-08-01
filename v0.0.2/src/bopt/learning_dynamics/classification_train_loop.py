import code
import json
import math
import os
from collections import defaultdict
from itertools import product

import torch
from tqdm import tqdm

from bopt.learning_dynamics.constants import TASK_LOSS, L1, ENTROPY
from bopt.learning_dynamics.dynamics_batch import dynamics_batch
from bopt.training import TrainingState
from bopt.training.classification_eval_loop import eval_classification
from bopt.training.saving import save_classification_checkpoint
from bopt.training.setup import ClassificationTrainingSetup
from bopt.training.utils import load_forever
from bopt.learning_dynamics.saving import save_learning_dynamics_log
from bopt.unigram_lm_tokenizers.encoding.forward_encoding import NONEDGE_ID, PADEDGE_ID


def train_classification(setup: ClassificationTrainingSetup):
    with open(os.path.join(setup.args.output_directory, "log.json"), "wt") as f:
        pass
    step = 0
    windowed_loss = []

    # dynamics logging stuff
    attention_dynamics = defaultdict(list)  # key is a four tuple (layer, head, src, tgt), value is a list of attentions
    attention_grad_dynamics = defaultdict(list)
    conditional_marginal_dynamics = defaultdict(list)  # key is two tuple (src, tgt), value is a list of cmarginals
    conditional_marginal_grad_dynamics = defaultdict(list)
    weights_dynamics = defaultdict(list) # key is single unit
    weights_grad_dynamics = defaultdict(list)
    attention_std_dynamics = defaultdict(list)
    attention_grad_std_dynamics = defaultdict(list)

    best_dev_acc = -1
    windowed_loss_avg = math.inf

    bar = tqdm(enumerate(load_forever(setup.train_dataloader)),total=setup.args.train_steps * setup.args.train_batch_size // setup.args.gpu_batch_size)
    for raw_step, (epoch, batch) in bar:
        # training state
        bar.set_description_str(f"Epoch={epoch} Step={step} Loss={sum(windowed_loss) / len(windowed_loss) if len(windowed_loss) else windowed_loss_avg:.2f}({len(windowed_loss) * setup.args.gpu_batch_size:>4d} exs)")
        state = TrainingState(step, epoch, bar.format_dict['elapsed'])

        # maybe evaluate
        if ((raw_step) % (setup.args.train_batch_size // setup.args.gpu_batch_size) == 0) and (
                step % setup.args.eval_steps == 0):
            setup.classifier.eval()
            with torch.no_grad():
                eval_metrics = eval_classification(setup, state)
            setup.classifier.train()

            # early stopping
            if eval_metrics["dev_accuracy"] > best_dev_acc:
                best_dev_acc = eval_metrics["dev_accuracy"]
                save_classification_checkpoint(setup.args.output_directory, "checkpoint-early-stopping", state,
                                               setup.classifier)

            # save log
            with open(os.path.join(setup.args.output_directory, "log.json"), "at") as f:
                logline = {
                    "step": step,
                    "epoch": epoch,
                    "elapsed": state.elapsed,
                    "train_loss": windowed_loss_avg,
                    "model_lr": setup.optimizer.named_param_groups["model_decay"]["lr"]
                }
                if setup.args.input_tokenizer_model in ["unigram", "nulm"] and setup.args.input_tokenizer_learning_rate: # only do this if training
                    logline["tokenizer_lr"] = setup.optimizer.named_param_groups["tokenizer"]["lr"]
                logline.update(eval_metrics)
                print(json.dumps(logline), file=f)
                print(logline)

        if step >= setup.args.train_steps:
            break

        # training
        ids, sentences, labels = batch
        loss = 0

        # dynamics logging
        loss = dynamics_batch(setup, step, raw_step, ids, sentences, labels)
        # do backward
        loss.backward()

        # maybe step optimizer
        if (raw_step + 1) %  (setup.args.train_batch_size // setup.args.gpu_batch_size) == 0:
            # if logging learning dynamics, do it right before stepping
            if not (setup.classifier.model.bert.embeddings.word_embeddings.weight.grad[setup.classifier.model.config.pad_token_id] == 0).all():
                raise AssertionError
            setup.optimizer.step()
            setup.classifier.model.zero_grad()
            if setup.args.input_tokenizer_model in ["unigram", "nulm"] and setup.args.input_tokenizer_learning_rate: # only do this if training
                setup.classifier.input_tokenizer.zero_grad()
                setup.classifier.input_tokenizer.clamp_weights()
            step += 1

        # maybe step scheduler
        windowed_loss.append(loss.item())
        if len(windowed_loss) >= (setup.args.lr_adjustment_window_size // setup.args.gpu_batch_size):
            if setup.args.annealing > 0 and setup.args.annealing_start_steps <= step <= setup.args.annealing_end_steps:
                setup.scheduler._reset()
                # reset so never step down due to increasing entropy loss factor, and continue with new baseline after
                # the annealing factor is maxed out
            windowed_loss_avg = sum(windowed_loss) / len(windowed_loss)
            setup.scheduler.step(windowed_loss_avg)
            windowed_loss = []


    save_classification_checkpoint(setup.args.output_directory, "checkpoint-final", state, setup.classifier)