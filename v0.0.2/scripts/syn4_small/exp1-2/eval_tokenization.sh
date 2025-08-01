#!/usr/bin/env bash
EXPID="1-2"
mkdir -p ${BLU_ARTIFACTS}/boptv2/syn4_small/exp${EXPID}
DATA_PREFIX=${BLU_CORPORA}/vopt/syn/4/small
ARTIFACT_PREFIX=${BLU_ARTIFACTS}/boptv2/syn4_small/exp${EXPID}
SCRIPT_PREFIX=${HOME}/jhu/bopt/v0.0.2/scripts/syn4_small/exp${EXPID}

for SEED in 42 44 46
do
for SIZE in 768
do
for L1 in 0.1
do
for INPUT_NAME in train dev
do
for CKPT in checkpoint-early-stopping checkpoint-final
do
OUTPUT_DIR=${ARTIFACT_PREFIX}/${SEED}/${SIZE}/${L1}
CHECKPOINT_DIR=${OUTPUT_DIR}/${CKPT}

python3 -um bopt.tokenization.evaluate_tokenization \
    ${DATA_PREFIX}/${INPUT_NAME}.1best.tokenizations.jsonl \
    ${CHECKPOINT_DIR}/${INPUT_NAME}.1best.tokenizations.jsonl > ${CHECKPOINT_DIR}/${INPUT_NAME}.1best.tokenizations.f1.json

echo ${CHECKPOINT_DIR}/${INPUT_NAME}.1best.tokenizations.f1.json
cat ${CHECKPOINT_DIR}/${INPUT_NAME}.1best.tokenizations.f1.json

done
done
done
done
done
