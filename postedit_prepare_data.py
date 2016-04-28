#!/usr/bin/env python

import numpy as np
from language_utils import GermanPreprocessor, GermanPostprocessor
from learning_utils import log, load_tokenized


def convert_to_edits(source, target):
    keep = '<keep>'
    delete = '<delete>'
    

    lev = np.zeros([len(source)+1, len(target)+1])
    edits = [ [ [] for _ in range(len(target)+1)] for _ in range(len(source)+1)]
    
    for i in range(len(source)+1):
        lev[i, 0] = i
        edits[i][0] = [delete for _ in range(i)]

    for j in range(len(target)+1):
        lev[0,j] = j
        edits[0][j] = target[:j]

    for j in range(1,len(target)+1):
        for i in range(1, len(source)+1):

            if source[i-1] == target[j-1]:
                keep_cost = lev[i-1,j-1]
            else:
                keep_cost = np.inf

            delete_cost = lev[i-1,j] + 1
            insert_cost = lev[i, j-1] + 1

            lev[i, j] = min(keep_cost, delete_cost, insert_cost)

            if lev[i, j] == keep_cost:
                edits[i][j] = edits[i-1][j-1] + [keep]

            elif lev[i, j] == delete_cost:
                edits[i][j] = edits[i-1][j] + [delete]

            else:
                edits[i][j] = edits[i][j-1] + [target[j-1]]

    return edits[-1][-1]


if __name__ == '__main__':
    #print convert_to_edits(["hello", "john"], ["hi", "john", "how"])

    import argparse

    parser = argparse.ArgumentParser(description="Convert postediting target data to sequence of edits")
    parser.add_argument("--translated-sentences", type=argparse.FileType('r'), required=True)
    parser.add_argument("--target-sentences", type=argparse.FileType('r'), required=True)
    parser.add_argument("--target-german", type=bool, default=False)
    
    args = parser.parse_args()

    preprocess=None
    if args.target_german:
        preprocess = GermanPreprocessor()
    
    trans_sentences = load_tokenized(args.translated_sentences, preprocess=preprocess)
    tgt_sentences = load_tokenized(args.target_sentences, preprocess=preprocess)
    
    for trans, tgt in zip(trans_sentences, tgt_sentences):
        edits = convert_to_edits(trans, tgt)
        print " ".join(edits)
    
    
    