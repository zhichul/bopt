from bopt.integerize import Integerizer
from bopt.unigram_lm_tokenizers.encoding.forward_encoding import integerize_for_forward
from bopt.unigram_lm_tokenizers.modeling.unigramlm import UnigramLM
from bopt.unigram_lm_tokenizers.utils.encoding import convert_to_backward_encoding
from bopt.unigram_lm_tokenizers.utils.printing import print_lattice

import torch


def test():
    vocabulary = Integerizer(
        [
            "[UNK]",
            "h",
            "a",
            "t",
            "e",
            "hat",
            "hate",
            "at",
            "ate",
        ]
    )
    log_potentials = torch.tensor([0.0] * len(vocabulary)).unsqueeze(-1)
    unigramlm = UnigramLM(len(vocabulary), log_potentials)

    # forward encoding
    encoding = integerize_for_forward(["hate"], 1, 4, 5, vocabulary,
                                      space_character=" ", split_on_space=False, add_dummy_space_start=False)
    print_lattice(encoding, vocabulary)
    print(encoding.size())
    output_potentials = unigramlm(encoding)
    print_lattice(encoding, vocabulary, log_potentials=output_potentials)

    # backward encoding
    encoding = convert_to_backward_encoding(encoding)
    print_lattice(encoding, vocabulary)
    print(encoding.size())
    output_potentials = unigramlm(encoding)
    print_lattice(encoding, vocabulary, log_potentials=output_potentials)

def test1():
    vocabulary = Integerizer(
        [
            "[UNK]",
            "h",
            "a",
            "t",
            "e",
            "hat",
            "hate",
            "at",
            "ate",
        ]
    )
    log_potentials = torch.tensor([0.0] * len(vocabulary)).unsqueeze(-1)
    unigramlm = UnigramLM(len(vocabulary), log_potentials)

    # forward encoding
    encoding = integerize_for_forward(["hate hat ate a at"], 2, 4, 10, vocabulary,
                                      space_character=" ", split_on_space=True, remove_space=True, add_dummy_space_start=False)
    print_lattice(encoding, vocabulary)
    print(encoding.size())
    output_potentials = unigramlm(encoding)
    print_lattice(encoding, vocabulary, log_potentials=output_potentials)

    # backward encoding
    encoding = convert_to_backward_encoding(encoding)
    print_lattice(encoding, vocabulary)
    print(encoding.size())
    output_potentials = unigramlm(encoding)
    print_lattice(encoding, vocabulary, log_potentials=output_potentials)

if __name__ == "__main__":
    test()
    test1()