import os, glob, sys
import pandas as pd
import numpy as np
from pathlib import Path

import argparse

def get_arguments():
    parser = argparse.ArgumentParser(description='insert columns of trialwise eye-tracking QC metrics to cleaned up *events.tsv files for THINGS dataset')
    parser.add_argument('--et_path', type=str, required=True, help='absolute path to directory that contains events files with eye-tracking metrics')
    parser.add_argument('--behav_path', type=str, required=True, help='absolute path to directory that contains events files with cleaned up behavioural metrics')
    parser.add_argument('--run_dir', default='', type=str, help='absolute path to main code directory')
    parser.add_argument('--out_path', type=str, default='./test.tsv', help='absolute path to output directory')
    args = parser.parse_args()

    return args


def main():
    '''
    This script inserts trial-wise eye-tracking QC metrics into events files for the THINGS dataset.
    It inserts these metrics into event files whose behavioural metrics were corrected
    to account for sessions administered out of order.
    '''

    args = get_arguments()
    # e.g., (elm): /unf/eyetracker/neuromod/triplets/sourcedata
    et_path = args.et_path
    behav_path = args.behav_path
    out_path = args.out_path

    col2add_list = ['gaze_confidence_ratio_cThresh0.9',
                    'gaze_count',
                    'fixation_compliance_ratio_deg1',
                    'fixation_compliance_ratio_deg2',
                    'fixation_compliance_ratio_deg3']

    behav_file_list = glob.glob(f'{behav_path}/sub-0*/ses-0*/sub-*_events.tsv')

    for b_file in behav_file_list:
        sub, ses, task, run, _ = os.path.basename(b_file).split('_')
        df_b = pd.read_csv(b_file, sep = '\t')

        et_file = glob.glob(f'{et_path}/{sub}_{ses}_*_task-thingsmemory_{run}_events.tsv')
        if len(et_file) == 1:

            df_et = pd.read_csv(et_file[0], sep = '\t')

            for new_c in col2add_list:
                df_b.insert(df_b.shape[1], f'{new_c}', df_et[new_c], allow_duplicates=True)
        else:
            for new_c in col2add_list:
                df_b.insert(df_b.shape[1], f'{new_c}', np.NAN, allow_duplicates=True)

        out_dir = f'{out_path}/{sub}/{ses}'
        Path(out_dir).mkdir(parents=True, exist_ok=True)

        out_name = f'{out_dir}/{sub}_{ses}_task-thingsmemory_{run}_events.tsv'
        df_b.to_csv(out_name, sep='\t', header=True, index=False)


if __name__ == '__main__':
    sys.exit(main())
