
function lattice_tokenize () {
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
  for L1 in 0.01 0.1 1.0
  do
  for SEED in 42 44 46 48 50 52 54 56 58 60
  do
  OUTPUT_DIR=${ARTIFACT_PREFIX}/${SEED}/${SIZE}/${L1}/${DATA}
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
      --report_reference \
      --input_mode json \
      < ${DATA_PREFIX}/${INPUT_NAME}.1best.tokenizations.jsonl \
      > ${CHECKPOINT_DIR}/${INPUT_NAME}.1best.tokenizations.jsonl

  done
  done
  done
  done
  done
  done
}

function lattice_eval_tokenization () {
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
  for L1 in 0.01 0.1 1.0
  do
  for SEED in 42 44 46 48 50 52 54 56 58 60
  do
  OUTPUT_DIR=${ARTIFACT_PREFIX}/${SEED}/${SIZE}/${L1}/${DATA}
  CHECKPOINT_DIR=${OUTPUT_DIR}/${CKPT}

  python3 -um bopt.tokenization.evaluate_tokenization \
      ${DATA_PREFIX}/${INPUT_NAME}.1best.tokenizations.jsonl \
      ${CHECKPOINT_DIR}/${INPUT_NAME}.1best.tokenizations.jsonl \
      --report_reference \
      --categories_file ${DATA_PREFIX}/${INPUT_NAME}.tokenization_categories.jsonl > ${CHECKPOINT_DIR}/${INPUT_NAME}.1best.tokenizations.f1.json

  echo ${CHECKPOINT_DIR}/${INPUT_NAME}.1best.tokenizations.f1.json
  cat ${CHECKPOINT_DIR}/${INPUT_NAME}.1best.tokenizations.f1.json

  done
  done
  done
  echo ""
  done
  echo ""
  done
  echo ""
  done
}


function unigram_tokenize () {
  DATA_PREFIX=${BLU_CORPORA}/vopt/syn/4/small
  ARTIFACT_PREFIX=${BLU_ARTIFACTS}/boptv2/syn4_small/exp${EXPID}
  SCRIPT_PREFIX=${HOME}/jhu/bopt/v0.0.2/scripts/syn4_small/exp${EXPID}

  for DATA in 100 500 small full
  do
  for SIZE in 768
  do
  for INPUT_NAME in train.100 dev test
  do
  for CKPT in checkpoint-final
  do
  for VSIZE in 50 100 200 400
  do
  for SEED in 42 44 46 48 50 52 54 56 58 60
  do
  OUTPUT_DIR=${ARTIFACT_PREFIX}/${SEED}/${SIZE}/${VSIZE}/${DATA}
  CHECKPOINT_DIR=${OUTPUT_DIR}/${CKPT}

  CUDA_VISIBLE_DEVICES=1 python3 -O -um bopt.tokenize \
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
      --report_reference \
      --input_mode json \
      < ${DATA_PREFIX}/${INPUT_NAME}.1best.tokenizations.jsonl \
      > ${CHECKPOINT_DIR}/${INPUT_NAME}.1best.tokenizations.jsonl

  done
  done
  done
  done
  done
  done
}

function unigram_eval_tokenization () {
  DATA_PREFIX=${BLU_CORPORA}/vopt/syn/4/small
  ARTIFACT_PREFIX=${BLU_ARTIFACTS}/boptv2/syn4_small/exp${EXPID}
  SCRIPT_PREFIX=${HOME}/jhu/bopt/v0.0.2/scripts/syn4_small/exp${EXPID}

  for DATA in 100 500 small full
  do
  for SIZE in 768
  do
  for INPUT_NAME in train.100 dev test
  do
  for CKPT in checkpoint-final
  do
  for VSIZE in 50 100 200 400
  do
  for SEED in 42 44 46 48 50 52 54 56 58 60
  do
  OUTPUT_DIR=${ARTIFACT_PREFIX}/${SEED}/${SIZE}/${VSIZE}/${DATA}
  CHECKPOINT_DIR=${OUTPUT_DIR}/${CKPT}

  python3 -um bopt.tokenization.evaluate_tokenization \
      ${DATA_PREFIX}/${INPUT_NAME}.1best.tokenizations.jsonl \
      ${CHECKPOINT_DIR}/${INPUT_NAME}.1best.tokenizations.jsonl \
      --report_reference \
      --categories_file ${DATA_PREFIX}/${INPUT_NAME}.tokenization_categories.jsonl > ${CHECKPOINT_DIR}/${INPUT_NAME}.1best.tokenizations.f1.json

  echo ${CHECKPOINT_DIR}/${INPUT_NAME}.1best.tokenizations.f1.json
  cat ${CHECKPOINT_DIR}/${INPUT_NAME}.1best.tokenizations.f1.json

  done
  done
  done
  echo ""
  done
  echo ""
  done
  echo ""
  done
}