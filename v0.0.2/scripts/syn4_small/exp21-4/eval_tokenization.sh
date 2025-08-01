#!/usr/bin/env bash
source vars.sh
DATA_PREFIX=${BLU_CORPORA}/vopt/syn/4/small
ARTIFACT_PREFIX=${BLU_ARTIFACTS}/boptv2/syn4_small/exp${EXPID}
SCRIPT_PREFIX=${HOME}/jhu/bopt/v0.0.2/scripts/syn4_small/exp${EXPID}

for DATA in 100 500 small full
do
for SIZE in 768
do
for INPUT_NAME in train.100 dev test
do
for CKPT in checkpoint-early-stopping checkpoint-final
do
for L1 in 0.01
do
for SEED in 42 44 46
do
for LR in 0.00 6.25e-5
do
OUTPUT_DIR=${ARTIFACT_PREFIX}/${SEED}/${SIZE}/${L1}/${DATA}/${LR}
CHECKPOINT_DIR=${OUTPUT_DIR}/${CKPT}

python3 -um bopt.tokenization.evaluate_tokenization \
    ${DATA_PREFIX}/${INPUT_NAME}.1best.tokenizations.jsonl \
    ${CHECKPOINT_DIR}/${INPUT_NAME}.1best.tokenizations.jsonl \
    --categories_file ${DATA_PREFIX}/${INPUT_NAME}.tokenization_categories.jsonl > ${CHECKPOINT_DIR}/${INPUT_NAME}.1best.tokenizations.f1.json

echo ${CHECKPOINT_DIR}/${INPUT_NAME}.1best.tokenizations.f1.json
cat ${CHECKPOINT_DIR}/${INPUT_NAME}.1best.tokenizations.f1.json

done
done
done
done
echo ""
done
echo ""
done
echo ""
done
