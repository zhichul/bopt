#!/usr/bin/env bash
EXPID="23-2"
mkdir -p ${BLU_ARTIFACTS}/boptv2/syn4_small/exp${EXPID}
DATA_PREFIX=${BLU_CORPORA}/vopt/syn/4/small
ARTIFACT_PREFIX=${BLU_ARTIFACTS}/boptv2/syn4_small/exp${EXPID}
SCRIPT_PREFIX=${HOME}/jhu/bopt/v0.0.2/scripts/syn4_small/exp${EXPID}

for SEED in 42 44 46
do
for SIZE in 768
do
for VSIZE in 50 100 200 400
do
for N in 10
do
for DATA in small 100 500 full
do
for INPUT_NAME in train dev test
do
for CKPT in checkpoint-early-stopping checkpoint-final
do
OUTPUT_DIR=${ARTIFACT_PREFIX}/${SEED}/${SIZE}/${VSIZE}/${N}best/${DATA}
CHECKPOINT_DIR=${OUTPUT_DIR}/${CKPT}

python3 -O -um bopt.tokenize \
    --input_vocab ${CHECKPOINT_DIR}/learned_vocab.txt \
    --input_tokenizer_weights ${CHECKPOINT_DIR}/learned_vocab.txt \
    --input_tokenizer_model unigram \
    --input_tokenizer_mode 1best \
    --special_tokens "[UNK]" "[CLS]" "[SEP]" "[PAD]" "[MASK]" "[WBD]" "[SP1]" "[SP2]" "[SP3]" "[SP4]" "[SP5]" \
    --pad_token "[PAD]" \
    \
    --max_blocks 1 \
    --max_unit_length 9 \
    --max_block_length 12 \
    --space_character " " \
    --remove_space \
    --split_on_space \
    < ${DATA_PREFIX}/${INPUT_NAME}.txt \
    > ${CHECKPOINT_DIR}/${INPUT_NAME}.1best.tokenizations.jsonl

done
done
done
done
done
done
done