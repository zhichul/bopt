#!/usr/bin/env bash
EXPID="22-3"
ARTIFACT_PREFIX=${BLU_ARTIFACTS}/boptv2/syn4_small/exp${EXPID}

rm -f hyperopt-results.json
touch hyperopt-results.json
for DATA in 100
do
touch hyperopt.${DATA}.tmp
for SIZE in 768
do
for VSIZE in 50
do
for DROPOUT in 0.1 0.2 0.4 #0.0
do
for SEED in 42 44 46
do
OUTPUT_DIR=${ARTIFACT_PREFIX}/${SEED}/${SIZE}/${VSIZE}/${DATA}/${DROPOUT}
python3 -m experiments.scripts.best_dev \
  ${OUTPUT_DIR}/log.json \
  dev_accuracy max \
  test_accuracy \
  --output_json \
  --add_field vsize ${VSIZE} \
  --add_field dropout ${DROPOUT} \
  --add_field seed ${SEED} >> hyperopt.${DATA}.tmp
done
done
done
done
python3 -m experiments.scripts.best_dev \
  hyperopt.${DATA}.tmp dev_accuracy max \
  test_accuracy \
  vsize \
  dropout \
  seed \
  --output_json \
  --add_field data ${DATA} > hyperopt.${DATA}.results.tmp \

python3 -m experiments.scripts.json_join \
  --path "${BLU_ARTIFACTS}/boptv2/syn4_small/exp22/{0}/${SIZE}/{1}/${DATA}/checkpoint-final/test.1best.tokenizations.f1.json" \
  --src hyperopt.${DATA}.results.tmp \
  --join_by seed vsize \
  --fields \
  token_precision \
  token_recall \
  token_f1 \
  boundary_precision \
  boundary_recall \
  boundary_f1 >> hyperopt-results.json

rm -f hyperopt.${DATA}.results.tmp
rm -f hyperopt.${DATA}.tmp
done
