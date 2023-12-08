import os, glob, sys, json
import pandas as pd
import numpy as np
from scipy.spatial.distance import pdist

from pathlib import Path

import matplotlib
matplotlib.use('pdf')
import matplotlib.pyplot as plt

import argparse

def get_arguments():
    parser = argparse.ArgumentParser(description='clean up, label, QC and bids-formats the triplets eye tracking dataset')
    parser.add_argument('--in_path', type=str, required=True, help='absolute path to directory that contains all data (sourcedata)')
    parser.add_argument('--is_final', action='store_true', default=False, help='if true, export drift-corrected gaze into bids format')
    parser.add_argument('--out_path', type=str, required=True, help='absolute path to output directory')
    args = parser.parse_args()

    return args


def create_gaze_path(row, file_path):
    '''
    for each run, create path to deserialized gaze file
    '''
    s = row['subject']
    ses = row['session']
    task = row["task"]

    task_root = file_path.split('/')[-1]
    if task_root == 'mario3':
        task = 'task-mario3'

    return f'{file_path}/{s}/{ses}/{s}_{ses}_{row["run"]}_{row["file_number"]}_{task}_gaze2D.npz'


def create_event_path(row, file_path, log=False):
    '''
    for each run, create path to events.tsv file
    '''
    s = row['subject']
    ses = row['session']
    if log:
        return f'{file_path}/{s}/{ses}/{s}_{ses}_{row["file_number"]}.log'
    else:
        if row['task'] in ['task-bar', 'task-rings', 'task-wedges', 'task-flocdef', 'task-flocalt']:
            return f'{file_path}/{s}/{ses}/{s}_{ses}_{row["file_number"]}_{row["task"]}_events.tsv'
        else:
            return f'{file_path}/{s}/{ses}/{s}_{ses}_{row["file_number"]}_{row["task"]}_{row["run"]}_events.tsv'


def create_ip_path(row, file_path):
    '''
    for each run, create path to info.player.json file
    '''
    s = row['subject']
    ses = row['session']
    r = row['run']
    t = row['task']
    fnum = row['file_number']

    if row['task'] in ['task-bar', 'task-rings', 'task-wedges', 'task-flocdef', 'task-flocalt']:
        return f'{file_path}/{s}/{ses}/{s}_{ses}_{fnum}.pupil/{t}/000/info.player.json'
    else:
        return f'{file_path}/{s}/{ses}/{s}_{ses}_{fnum}.pupil/{t}_{r}/000/info.player.json'


def get_onset_time(log_path, run_num, ip_path, gz_ts):
    onset_time_dict = {}
    TTL_0 = -1
    has_lines = True

    with open(log_path) as f:
        lines = f.readlines()
        if len(lines) == 0:
            has_lines = False
        for line in lines:
            if "fMRI TTL 0" in line:
                TTL_0 = line.split('\t')[0]
            elif "saved wide-format data to /scratch/neuromod/data" in line:
                rnum = line.split('\t')[-1].split('_')[-2]
                onset_time_dict[rnum] = float(TTL_0)
            elif "class 'src.tasks.videogame.VideoGameMultiLevel'" in line:
                rnum = line.split(': ')[-2].split('_')[-1]
                onset_time_dict[rnum] = float(TTL_0)
            elif "class 'src.tasks.localizers.FLoc'" in line:
                rnum = run2task_mapping['floc'][line.split(': ')[-2]]
                onset_time_dict[rnum] = float(TTL_0)
            elif "class 'src.tasks.retinotopy.Retinotopy'" in line:
                rnum = run2task_mapping['retino'][line.split(': ')[-2]]
                onset_time_dict[rnum] = float(TTL_0)

    if has_lines:
        o_time = onset_time_dict[run_num]

        with open(ip_path, 'r') as f:
            iplayer = json.load(f)
        sync_ts = iplayer['start_time_synced_s']
        syst_ts = iplayer['start_time_system_s']

        is_sync_gz = (gz_ts-sync_ts)**2 < (gz_ts-syst_ts)**2
        is_sync_ot = (o_time-sync_ts)**2 < (o_time-syst_ts)**2
        if is_sync_gz != is_sync_ot:
            if is_sync_ot:
                o_time += (syst_ts - sync_ts)
            else:
                o_time += (sync_ts - syst_ts)
    else:
        print('empty log file, onset time estimated from gaze timestamp')
        o_time = gz_ts

    return o_time


def reset_gaze_time(gaze, onset_time, conf_thresh=0.9):
    '''
    Realign gaze timestamps based on the task & eyetracker onset (triggered by fMRI run start)
    Export new list of gaze dictionaries (w task-aligned time stamps) and other metrics needed to perform drift correction
    '''
    # all gaze values (unfiltered)
    reset_gaze_list = []
    all_x = []
    all_y = []
    all_times = []
    all_conf = []

    # normalized distance (proportion of screen) between above-threshold gaze and fixation point
    # includes fixation and trial gaze
    clean_dist_x = []
    clean_dist_y = []
    clean_times = []
    clean_conf = [] # probably not needed since filtered...

    for gz_pt in gaze:
        timestp = gz_pt['timestamp'] - onset_time
        # exclude gaze & pupil gathered before task onset
        if timestp > 0.0:
            x_norm, y_norm = gz_pt['norm_pos']
            cfd = gz_pt['confidence']

            all_x.append(x_norm)
            all_y.append(y_norm)
            all_times.append(timestp)
            all_conf.append(cfd)

            gz_pt['reset_time'] = timestp
            reset_gaze_list.append(gz_pt)

            if cfd > conf_thresh:
                clean_dist_x.append(x_norm - 0.5)
                clean_dist_y.append(y_norm - 0.5)
                clean_conf.append(cfd)
                clean_times.append(timestp)

    return reset_gaze_list, (all_x, all_y, all_times, all_conf), (clean_dist_x, clean_dist_y, clean_times, clean_conf)


def get_single_distance(x1, y1, x2, y2, is_distance=False):

    norm_center = (0, 0) if is_distance else (0.5, 0.5)

    pre_x = (x1 - norm_center[0])*1280
    pre_y = (y1 - norm_center[1])*1024
    post_x = (x2 - norm_center[0])*1280
    post_y = (y2 - norm_center[1])*1024

    dist_in_pix = 4164 # in pixels

    vectors = np.array([[pre_x, pre_y, dist_in_pix], [post_x, post_y, dist_in_pix]])
    distance = np.rad2deg(np.arccos(1.0 - pdist(vectors, metric='cosine')))[0]

    return distance


def get_fixation_gaze_things(df_ev, clean_dist_x, clean_dist_y, clean_times, fix_period="image"):
    '''
    Identify gaze that correspond to periods of fixation
    Export median gaze position for each fixation, time stamp, and fixation QC metrics (for plotting)
    '''
    fix_dist_x = []
    fix_dist_y = []
    fix_times = []

    fix_metrics = {
        'gz_count': [],
        'gz_idx': [],
        'gz_dist_2_prev': [],
    }

    j = 0

    for i in range(df_ev.shape[0]):

        if fix_period == 'image':
            fixation_onset = df_ev['onset'][i]
            fixation_offset = fixation_onset + df_ev['duration'][i]
            onset_buffer = 0.0

        elif fix_period == 'isi':
            fixation_onset = df_ev['onset'][i] + df_ev['duration'][i]
            fixation_offset = fixation_onset + 1.49
            onset_buffer = 0.6

        elif fix_period == 'image+isi':
            fixation_onset = df_ev['onset'][i]
            fixation_offset = fixation_onset + df_ev['duration'][i] + 1.49
            onset_buffer = 0.0


        # add gaze from trial fixation period
        trial_fd_x = []
        trial_fd_y = []
        trial_ftimes = []
        while j < len(clean_times) and clean_times[j] < fixation_offset:
            # + 0.8 = 800ms (0.8s) after trial offset to account for saccade
            # if clean_times[j] > (fixation_onset + 0.8) and clean_times[j] < (fixation_offset - 0.1):
            if clean_times[j] > (fixation_onset + onset_buffer) and clean_times[j] < (fixation_offset - 0.1):
                trial_fd_x.append(clean_dist_x[j])
                trial_fd_y.append(clean_dist_y[j])
                trial_ftimes.append(clean_times[j])

            j += 1

        #if len(trial_fd_x) > 0:
        if len(trial_fd_x) > 20:
            med_x = np.median(trial_fd_x)
            med_y = np.median(trial_fd_y)

            prev_x, prev_y = (0.0, 0.0) if len(fix_dist_x) < 1 else (fix_dist_x[-1], fix_dist_y[-1])
            fix_metrics['gz_dist_2_prev'].append(get_single_distance(med_x, med_y, prev_x, prev_y, is_distance=True))

            fix_dist_x.append(med_x)
            fix_dist_y.append(med_y)
            fix_times.append(trial_ftimes[0])

            fix_metrics['gz_count'].append(len(trial_fd_x))
            fix_metrics['gz_idx'].append(i)

    return fix_dist_x, fix_dist_y, fix_times, fix_metrics


def add_metrics_2events(df_ev,
                        all_times,
                        all_conf,
                        all_x,
                        all_y,
                        all_x_aligned,
                        all_y_aligned,
                        conf_thresh=0.9,
                        strategy_name='previous_image+isi',
                        ):

    all_distances = get_distances_from_center(all_x_aligned, all_y_aligned)
    all_distances_raw = get_distances_from_center(all_x, all_y)

    metrics_per_trials = {}
    all_idx = 0

    first_fix_trialnum = None

    for i in range(df_ev.shape[0]):
        trial_number = df_ev['TrialNumber'][i]

        trial_onset = df_ev['onset'][i]
        trial_offset = trial_onset + df_ev['duration'][i]

        # skip first 0.6s of ISI during which subjects tend to blink
        isi_buffer = 0.0 if 'image+isi' in strategy_name else 0.6
        isi_onset = trial_offset + isi_buffer
        isi_offset = trial_offset + 1.49


        trial_confs = []
        trial_x = []
        trial_y = []
        trial_distance = []
        isi_confs = []
        isi_x = []
        isi_y = []
        isi_distances = []

        while all_idx < len(all_times) and all_times[all_idx] < isi_offset:
            if all_times[all_idx] > trial_onset:
                if all_times[all_idx] < trial_offset:
                    trial_confs.append(all_conf[all_idx])
                    trial_x.append(all_x[all_idx])
                    trial_y.append(all_y[all_idx])
                    trial_distance.append(all_distances[all_idx])

                elif all_times[all_idx] > isi_onset:
                    isi_confs.append(all_conf[all_idx])
                    isi_x.append(all_x[all_idx])
                    isi_y.append(all_y[all_idx])
                    isi_distances.append(all_distances[all_idx])

            all_idx += 1

        if 'image+isi' in strategy_name:
            fix_confs = trial_confs + isi_confs
            fix_x = trial_x + isi_x
            fix_y = trial_y + isi_y
            fix_distances = trial_distance + isi_distances
        elif 'image' in strategy_name:
            fix_confs = trial_confs
            fix_x = trial_x
            fix_y = trial_y
            fix_distances = trial_distance
        else:
            fix_confs = isi_confs
            fix_x = isi_x
            fix_y = isi_y
            fix_distances = isi_distances


        t_conf_filter = np.array(trial_confs) > conf_thresh
        t_sum = np.sum(t_conf_filter)
        if t_sum:
            trial_x_arr = np.array(trial_x)[t_conf_filter]
            trial_y_arr = np.array(trial_y)[t_conf_filter]
            trial_dist_arr = np.array(trial_distance)[t_conf_filter]
            t_med_x = np.median(trial_x_arr)
            t_med_y = np.median(trial_y_arr)
            t_dist_to_med = np.array(get_distances_from_center(trial_x_arr-t_med_x, trial_y_arr-t_med_y, is_distance=True))

        f_conf_filter = np.array(fix_confs) > conf_thresh
        f_sum = np.sum(f_conf_filter)
        if f_sum:
            if first_fix_trialnum is None:
                first_fix_trialnum = trial_number
            fix_x_arr = np.array(fix_x)[f_conf_filter]
            fix_y_arr = np.array(fix_y)[f_conf_filter]
            fix_dist_arr = np.array(fix_distances)[f_conf_filter]
            f_med_x = np.median(fix_x_arr)
            f_med_y = np.median(fix_y_arr)
            f_dist_to_med = np.array(get_distances_from_center(fix_x_arr-f_med_x, fix_y_arr-f_med_y, is_distance=True))


        metrics_per_trials[trial_number] = {
            'trial_gaze_count': len(trial_confs),
            'trial_gaze_conf_90': np.sum(np.array(trial_confs) > 0.9)/len(trial_confs) if len(trial_confs) > 0 else np.nan,
            'trial_gaze_conf_75': np.sum(np.array(trial_confs) > 0.75)/len(trial_confs) if len(trial_confs) > 0 else np.nan,
            'fix_gaze_count': len(fix_confs),
            'fix_gaze_conf_90': np.sum(np.array(fix_confs) > 0.9)/len(fix_confs) if len(fix_confs) > 0 else np.nan,
            'fix_gaze_conf_75': np.sum(np.array(fix_confs) > 0.75)/len(fix_confs) if len(fix_confs) > 0 else np.nan,

            f'trial_median_x_{conf_thresh}': t_med_x if t_sum else np.nan,
            f'trial_median_y_{conf_thresh}': t_med_y if t_sum else np.nan,
            'trial_fix_compliance_ratio_deg0.5': np.sum(trial_dist_arr < 0.5)/t_sum if t_sum else np.nan,
            'trial_fix_compliance_ratio_deg1': np.sum(trial_dist_arr < 1.0)/t_sum if t_sum else np.nan,
            'trial_fix_compliance_ratio_deg2': np.sum(trial_dist_arr < 2.0)/t_sum if t_sum else np.nan,
            'trial_fix_compliance_ratio_deg3': np.sum(trial_dist_arr < 3.0)/t_sum if t_sum else np.nan,

            'trial_dist2med_ratio_deg0.5': np.sum(t_dist_to_med < 0.5)/t_sum if t_sum else np.nan,
            'trial_dist2med_ratio_deg1': np.sum(t_dist_to_med < 1.0)/t_sum if t_sum else np.nan,
            'trial_dist2med_ratio_deg2': np.sum(t_dist_to_med < 2.0)/t_sum if t_sum else np.nan,
            'trial_dist2med_ratio_deg3': np.sum(t_dist_to_med < 3.0)/t_sum if t_sum else np.nan,

            f'fix_median_x_{conf_thresh}': f_med_x if f_sum else np.nan,
            f'fix_median_y_{conf_thresh}': f_med_y if f_sum else np.nan,
            'fix_dist2med_ratio_deg0.5': np.sum(f_dist_to_med < 0.5)/f_sum if f_sum else np.nan,
            'fix_dist2med_ratio_deg1': np.sum(f_dist_to_med < 1.0)/f_sum if f_sum else np.nan,
            'fix_dist2med_ratio_deg2': np.sum(f_dist_to_med < 2.0)/f_sum if f_sum else np.nan,
            'fix_dist2med_ratio_deg3': np.sum(f_dist_to_med < 3.0)/f_sum if f_sum else np.nan,
            }

    metrics_per_trials[0] = metrics_per_trials[first_fix_trialnum]

    '''
    Insert drift correction strategy name
    '''
    df_ev['drift_correction_strategy'] = df_ev.apply(lambda row: strategy_name, axis=1)
    df_ev['confidence_threshold'] = df_ev.apply(lambda row: conf_thresh, axis=1)

    '''
    Insert gaze count: fixation and image
    '''
    if 'image+isi' in strategy_name:
        fix_dur = 1.49 + 2.98
    elif 'image' in strategy_name:
        fix_dur = 2.98
    else:
        fix_dur = 1.49 - isi_buffer
    num_back = 0 if 'current' in strategy_name else 1

    df_ev['fix_gaze_count_ratio'] = df_ev.apply(lambda row: (metrics_per_trials[row['TrialNumber']-num_back]['fix_gaze_count'])/(250*fix_dur), axis=1)
    df_ev['trial_gaze_count_ratio'] = df_ev.apply(lambda row: (metrics_per_trials[row['TrialNumber']]['trial_gaze_count'])/(250*2.98), axis=1)

    '''
    Insert gaze confidence ratio, out of all collected gaze (0.9 and 0.75 thresholds): fixation and image
    '''
    df_ev['fix_gaze_confidence_ratio_0.9'] = df_ev.apply(lambda row: metrics_per_trials[row['TrialNumber']-num_back]['fix_gaze_conf_90'], axis=1)
    df_ev['fix_gaze_confidence_ratio_0.75'] = df_ev.apply(lambda row: metrics_per_trials[row['TrialNumber']-num_back]['fix_gaze_conf_75'], axis=1)
    df_ev['trial_gaze_confidence_ratio_0.9'] = df_ev.apply(lambda row: metrics_per_trials[row['TrialNumber']]['trial_gaze_conf_90'], axis=1)
    df_ev['trial_gaze_confidence_ratio_0.75'] = df_ev.apply(lambda row: metrics_per_trials[row['TrialNumber']]['trial_gaze_conf_75'], axis=1)

    '''
    Insert distance between median positions, in deg of visual angle, between pre- and pos-isi /current and previous (excessive head motion)
    '''
    df_ev['median_dist_to_fixation_in_deg'] = df_ev.apply(lambda row: get_isi_distance(metrics_per_trials, row['TrialNumber'], num_back, conf_thresh), axis=1)
    df_ev['median_dist_to_previous_trial_in_deg'] = df_ev.apply(lambda row: get_isi_distance(metrics_per_trials, row['TrialNumber'], 1, conf_thresh, use_trial=True), axis=1)

    '''
    Insert fixation compliance ratios
    '''
    df_ev['trial_fixation_compliance_ratio_0.5'] = df_ev.apply(lambda row: metrics_per_trials[row['TrialNumber']]['trial_fix_compliance_ratio_deg0.5'], axis=1)
    df_ev['trial_fixation_compliance_ratio_1.0'] = df_ev.apply(lambda row: metrics_per_trials[row['TrialNumber']]['trial_fix_compliance_ratio_deg1'], axis=1)
    df_ev['trial_fixation_compliance_ratio_2.0'] = df_ev.apply(lambda row: metrics_per_trials[row['TrialNumber']]['trial_fix_compliance_ratio_deg2'], axis=1)
    df_ev['trial_fixation_compliance_ratio_3.0'] = df_ev.apply(lambda row: metrics_per_trials[row['TrialNumber']]['trial_fix_compliance_ratio_deg3'], axis=1)

    df_ev['trial_dist2med_ratio_0.5'] = df_ev.apply(lambda row: metrics_per_trials[row['TrialNumber']]['trial_dist2med_ratio_deg0.5'], axis=1)
    df_ev['trial_dist2med_ratio_1.0'] = df_ev.apply(lambda row: metrics_per_trials[row['TrialNumber']]['trial_dist2med_ratio_deg1'], axis=1)
    df_ev['trial_dist2med_ratio_2.0'] = df_ev.apply(lambda row: metrics_per_trials[row['TrialNumber']]['trial_dist2med_ratio_deg2'], axis=1)
    df_ev['trial_dist2med_ratio_3.0'] = df_ev.apply(lambda row: metrics_per_trials[row['TrialNumber']]['trial_dist2med_ratio_deg3'], axis=1)

    df_ev['fix_dist2med_ratio_0.5'] = df_ev.apply(lambda row: metrics_per_trials[row['TrialNumber']]['fix_dist2med_ratio_deg0.5'], axis=1)
    df_ev['fix_dist2med_ratio_1.0'] = df_ev.apply(lambda row: metrics_per_trials[row['TrialNumber']]['fix_dist2med_ratio_deg1'], axis=1)
    df_ev['fix_dist2med_ratio_2.0'] = df_ev.apply(lambda row: metrics_per_trials[row['TrialNumber']]['fix_dist2med_ratio_deg2'], axis=1)
    df_ev['fix_dist2med_ratio_3.0'] = df_ev.apply(lambda row: metrics_per_trials[row['TrialNumber']]['fix_dist2med_ratio_deg3'], axis=1)

    return df_ev, all_distances_raw, all_distances


def driftcorr_fromlast(fd_x, fd_y, f_times, all_x, all_y, all_times, previous_image=False):
    i = 0
    j = 0
    all_x_aligned = []
    all_y_aligned = []

    '''
    1: re-align from previous isi, current image or current image + isi median position
    2: re-align from previous image or previous image + isi median position
    '''
    gap = 2 if previous_image else 1

    for i in range(len(all_times)):
        while j < len(f_times)-gap and all_times[i] > f_times[j+gap]:
            j += 1
        all_x_aligned.append(all_x[i] - fd_x[j])
        all_y_aligned.append(all_y[i] - fd_y[j])

    return all_x_aligned, all_y_aligned


def get_distances_from_center(x, y, is_distance=False):
    '''
    if is_distance == True:
        x and y are relative distances from center, else they are normalized coordinates
    '''
    assert len(x) == len(y)

    dist_in_pix = 4164 # in pixels
    m_vecpos = np.array([0., 0., dist_in_pix])

    all_pos = np.stack((x, y), axis=1)
    if is_distance:
        gaze = (all_pos - 0.0)*(1280, 1024)
    else:
        gaze = (all_pos - 0.5)*(1280, 1024)
    gaze_vecpos = np.concatenate((gaze, np.repeat(dist_in_pix, len(gaze)).reshape((-1, 1))), axis=1)

    all_distances = []
    for gz_vec in gaze_vecpos:
        vectors = np.stack((m_vecpos, gz_vec), axis=0)
        distance = np.rad2deg(np.arccos(1.0 - pdist(vectors, metric='cosine')))[0]
        all_distances.append(distance)

    return all_distances


def get_isi_distance(metrics_dict, trial_num, num_back, conf_thresh, use_trial=False):

    fix_name = 'trial' if use_trial else 'fix'

    pre_x = (metrics_dict[trial_num-num_back][f'{fix_name}_median_x_{conf_thresh}'] - 0.5)*1280
    pre_y = (metrics_dict[trial_num-num_back][f'{fix_name}_median_y_{conf_thresh}'] - 0.5)*1024
    post_x = (metrics_dict[trial_num][f'trial_median_x_{conf_thresh}'] - 0.5)*1280
    post_y = (metrics_dict[trial_num][f'trial_median_y_{conf_thresh}'] - 0.5)*1024

    dist_in_pix = 4164 # in pixels

    vectors = np.array([[pre_x, pre_y, dist_in_pix], [post_x, post_y, dist_in_pix]])
    distance = np.rad2deg(np.arccos(1.0 - pdist(vectors, metric='cosine')))[0]

    return distance


def driftCorr_ET(row, out_path, is_final=False):

    task_root = out_path.split('/')[-1]

    [sub, ses, fnum, task_type, run_num, appendix] = os.path.basename(row['events_path']).split('_')
    print(sub, ses, fnum, task_type, run_num)

    if is_final:
        outpath_events = f'{out_path}/Events_files_enhanced'
        Path(outpath_events).mkdir(parents=True, exist_ok=True)
        out_file = f'{outpath_events}/{sub}_{ses}_{fnum}_{task_type}_{run_num}_events.tsv'
    else:
        outpath_fig = os.path.join(out_path, 'DC_gaze')
        Path(outpath_fig).mkdir(parents=True, exist_ok=True)
        out_file = f'{out_path}/DC_gaze/{sub}_{ses}_{run_num}_{fnum}_{task_type}_DCplot.png'

    if not os.path.exists(out_file):
        #if True:
        try:
            run_event = pd.read_csv(row['events_path'], sep = '\t', header=0)
            run_gaze = np.load(row['gaze_path'], allow_pickle=True)['gaze2d']

            # identifies logged run start time (mri TTL 0) on clock that matches the gaze using info.player.json
            onset_time = get_onset_time(row['log_path'], row['run'], row['infoplayer_path'], run_gaze[10]['timestamp'])

            gaze_threshold = row['pupilConf_thresh'] if not pd.isna(row['pupilConf_thresh']) else 0.9
            reset_gaze_list, all_vals, clean_vals  = reset_gaze_time(run_gaze, onset_time, gaze_threshold)
            # normalized position (x and y), time (s) from onset and confidence for all gaze
            all_x, all_y, all_times, all_conf = all_vals
            all_times_arr = np.array(all_times)
            # distance from central fixation point for all gaze above confidence threshold
            clean_dist_x, clean_dist_y, clean_times, clean_conf = clean_vals

            strategies = {
                'current_image': ('image', False),
                'previous_image': ('image', True),
                'previous_isi': ('isi', False),
                'current_image+isi': ('image+isi', False),
                'previous_image+isi': ('image+isi', True),
                }
            '''
            sub_strategy = {
                'sub-01': strategies['current_image+isi'],
                'sub-02': strategies['previous_isi'],
                'sub-03': strategies['current_image'],
                'sub-06': strategies['previous_isi'],
                }

            strategy_name = sub_strategy[sub]
            '''
            #strategy_name = 'current_image+isi'
            strategy_name = 'previous_image+isi'
            strategy = strategies[strategy_name]

            fix_dist_x, fix_dist_y, fix_times, fix_metrics = get_fixation_gaze_things(run_event, clean_dist_x, clean_dist_y, clean_times, fix_period=strategy[0])
            all_x_aligned, all_y_aligned = driftcorr_fromlast(fix_dist_x, fix_dist_y, fix_times, all_x, all_y, all_times, previous_image=strategy[1])

            run_event, all_distInDeg, all_distInDeg_aligned = add_metrics_2events(
                                                                                  run_event,
                                                                                  all_times,
                                                                                  all_conf,
                                                                                  all_x,
                                                                                  all_y,
                                                                                  all_x_aligned,
                                                                                  all_y_aligned,
                                                                                  conf_thresh=gaze_threshold,
                                                                                  strategy_name=strategy_name,
                                                                                  )

            if is_final:
                # export final events files w metrics on eyetracking & fixation quality per trial
                run_event.to_csv(out_file, sep='\t', header=True, index=False)

                # Export drift-corrected gaze, realigned timestamps, and all other metrics (pupils, etc) to bids-compliant .tsv file
                # guidelines: https://bids-specification--1128.org.readthedocs.build/en/1128/modality-specific-files/eye-tracking.html#sidecar-json-document-_eyetrackjson
                col_names = ['eye_timestamp',
                             'eye1_x_coordinate', 'eye1_y_coordinate',
                             'eye1_confidence',
                             'eye1_x_coordinate_driftCorr', 'eye1_y_coordinate_driftCorr',
                             'eye1_pupil_x_coordinate', 'eye1_pupil_y_coordinate',
                             'eye1_pupil_diameter',
                             'eye1_pupil_ellipse_axes',
                             'eye1_pupil_ellipse_angle',
                             'eye1_pupil_ellipse_center'
                             ]

                final_gaze_list = []

                assert len(reset_gaze_list) == len(all_x_aligned)
                for i in range(len(reset_gaze_list)):
                    gaze_pt = reset_gaze_list[i]
                    assert gaze_pt['reset_time'] == all_times[i]

                    gaze_pt_data = [
                                    gaze_pt['reset_time'], # in s
                                    #round(gaze_pt['reset_time']*1000, 0), # int, in ms
                                    gaze_pt['norm_pos'][0], gaze_pt['norm_pos'][1],
                                    gaze_pt['confidence'],
                                    all_x_aligned[i], all_y_aligned[i],
                                    gaze_pt['base_data']['norm_pos'][0], gaze_pt['base_data']['norm_pos'][1],
                                    gaze_pt['base_data']['diameter'],
                                    gaze_pt['base_data']['ellipse']['axes'],
                                    gaze_pt['base_data']['ellipse']['angle'],
                                    gaze_pt['base_data']['ellipse']['center'],
                        ]

                    final_gaze_list.append(gaze_pt_data)

                df_gaze = pd.DataFrame(np.array(final_gaze_list, dtype=object), columns=col_names)

                bids_out_path = f'{out_path}/final_bids_DriftCor/{sub}/{ses}'
                Path(bids_out_path).mkdir(parents=True, exist_ok=True)
                gfile_path = f'{bids_out_path}/{sub}_{ses}_{task_type}_{run_num}_eyetrack.tsv.gz'
                if os.path.exists(gfile_path):
                    # just in case session's run is done twice... note: not bids...
                    gfile_path = f'{bids_out_path}/{sub}_{ses}_{task_type}_{fnum}_{run_num}_eyetrack.tsv.gz'
                df_gaze.to_csv(gfile_path, sep='\t', header=True, index=False, compression='gzip')

            else:
                '''
                plot DriftCorr QC figures
                '''
                fix_metrics['gz_trial_fixCom'] = []
                fix_metrics['gz_dist2prev'] = []
                fix_metrics['fix_gz_conf'] = []

                cutoff = '0.9' if gaze_threshold == 0.9 else '0.75'

                for i in fix_metrics['gz_idx']:
                    fix_metrics['gz_trial_fixCom'].append(run_event['trial_fixation_compliance_ratio_1.0'][i])
                    fix_metrics['fix_gz_conf'].append(run_event[f'fix_gaze_confidence_ratio_{cutoff}'][i])
                    fix_metrics['gz_dist2prev'].append(run_event['median_dist_to_previous_trial_in_deg'][i])

                vals2plot = {
                    #'col=gz_count': {
                    #    'values': fix_metrics['gz_count'],
                    #    'refs': ['G', 'H', 'I'],
                    #    'cmap': 'plasma_r',
                    #},
                    #'col=dist_from_previous': {
                    #    'values': fix_metrics['gz_dist_2_prev'],
                    #    'refs': ['J', 'K', 'L'],
                    #    'cmap': 'plasma',
                    #},
                    'col=fix_confidence': {
                        'values': fix_metrics['fix_gz_conf'],
                        'refs': ['G', 'H', 'I'],
                        'cmap': 'plasma_r',
                    },
                    'col=dist2previous': {
                        'values': fix_metrics['gz_dist2prev'],
                        'refs': ['J', 'K', 'L'],
                        'cmap': 'plasma',
                    },
                    'col=trial_fix_compliance': {
                        'values': fix_metrics['gz_trial_fixCom'],
                        'refs': ['M', 'N', 'O'],
                        'cmap': 'plasma_r',
                    },
                }

                clean_distInDeg = get_distances_from_center(clean_dist_x, clean_dist_y, is_distance=True)
                fix_distInDeg = get_distances_from_center(fix_dist_x, fix_dist_y, is_distance=True)

                color_metric = np.array(all_conf)
                s = color_metric.argsort()
                time_vals = np.array(all_times)[s]
                x_vals = np.array(all_x)[s]
                x_vals_align = np.array(all_x_aligned)[s]
                y_vals = np.array(all_y)[s]
                y_vals_align = np.array(all_y_aligned)[s]
                dist_vals = np.array(all_distInDeg)[s]
                dist_vals_align = np.array(all_distInDeg_aligned)[s]
                cm = color_metric[s]

                mosaic = """
                    ABC
                    DEF
                    GHI
                    JKL
                    MNO
                """
                fs = (20, 24.0)

                fig = plt.figure(constrained_layout=True, figsize=fs)
                ax_dict = fig.subplot_mosaic(mosaic)
                run_dur = int(run_event.iloc[-1]['onset'] + 20)

                ax_dict["A"].scatter(run_event['onset'].to_numpy(), run_event[f'trial_gaze_confidence_ratio_0.9'].to_numpy(), color='xkcd:dark blue', label='> 0.9')
                ax_dict["A"].scatter(run_event['onset'].to_numpy(), run_event[f'trial_gaze_confidence_ratio_0.75'].to_numpy(), color='xkcd:light green', label='> 0.75')
                ax_dict["A"].set_ylim(-0.1, 1.1)
                ax_dict["A"].set_xlim(0, run_dur)
                ax_dict["A"].legend()
                ax_dict["A"].set_title(f'{sub} {ses} {run_num} trialwise_confidence')

                ax_dict["B"].scatter(run_event['onset'].to_numpy(), run_event['trial_fixation_compliance_ratio_0.5'].to_numpy(), color='xkcd:navy blue', label='< 0.5 deg')
                ax_dict["B"].scatter(run_event['onset'].to_numpy(), run_event['trial_fixation_compliance_ratio_1.0'].to_numpy(), color='xkcd:bright green', label='< 1.0 deg')
                ax_dict["B"].scatter(run_event['onset'].to_numpy(), run_event['trial_fixation_compliance_ratio_2.0'].to_numpy(), color='xkcd:orange', label='< 2.0 deg')
                ax_dict["B"].set_ylim(-0.1, 1.1)
                ax_dict["B"].set_xlim(-0.1, run_dur)
                ax_dict["B"].legend()
                ax_dict["B"].set_title(f'{sub} {ses} {run_num} trialwise_fixCompliance')

                ax_dict["D"].scatter(time_vals, x_vals, s=10, color='xkcd:light grey', alpha=cm)
                ax_dict["D"].scatter(time_vals, x_vals_align, c=cm, s=10, cmap='terrain_r', alpha=0.2)#'xkcd:orange', alpha=all_conf)
                ax_dict["D"].set_ylim(-1.5, 2)
                ax_dict["D"].set_xlim(0, run_dur)
                ax_dict["D"].set_title(f'{sub} {ses} {run_num} gaze_x')

                ax_dict["E"].scatter(time_vals, y_vals, color='xkcd:light grey', alpha=cm)
                ax_dict["E"].scatter(time_vals, y_vals_align, c=cm, s=10, cmap='terrain_r', alpha=0.2)#'xkcd:orange', alpha=all_conf)
                ax_dict["E"].set_ylim(-1.5, 2)
                ax_dict["E"].set_xlim(0, run_dur)
                ax_dict["E"].set_title(f'{sub} {ses} {run_num} gaze_y')

                ax_dict["F"].scatter(time_vals, dist_vals, color='xkcd:light grey', alpha=cm)
                ax_dict["F"].scatter(time_vals, dist_vals_align, c=cm, s=10, cmap='terrain_r', alpha=0.2)#'xkcd:orange', alpha=all_conf)
                ax_dict["F"].set_ylim(-0.1, 20)
                ax_dict["F"].set_xlim(0, run_dur)
                ax_dict["F"].set_title(f'{sub} {ses} {run_num} dist2center_deg')

                for key in vals2plot:
                    refs = vals2plot[key]['refs']
                    color_metric = vals2plot[key]['values']
                    cmap = vals2plot[key]['cmap']

                    ax_dict[refs[0]].scatter(clean_times, clean_dist_x, color='xkcd:light blue', s=20, alpha=0.2)
                    ax_dict[refs[0]].scatter(fix_times, fix_dist_x, c=color_metric, cmap=cmap, s=20, alpha=1.0)
                    ax_dict[refs[0]].set_ylim(-2, 2)
                    ax_dict[refs[0]].set_xlim(0, run_dur)
                    ax_dict[refs[0]].set_title(f'{sub} {ses} {run_num} fix_distance_x {key}')

                    ax_dict[refs[1]].scatter(clean_times, clean_dist_y, color='xkcd:light blue', s=20, alpha=0.2)
                    ax_dict[refs[1]].scatter(fix_times, fix_dist_y, c=color_metric, cmap=cmap, s=20, alpha=1.0)
                    lb = np.min(fix_dist_y)-0.1 if np.min(fix_dist_y) < -2 else -2
                    hb = np.max(fix_dist_y)+0.1 if np.max(fix_dist_y) > 2 else 2
                    ax_dict[refs[1]].set_ylim(lb, hb)
                    ax_dict[refs[1]].set_xlim(0, run_dur)
                    ax_dict[refs[1]].set_title(f'{sub} {ses} {run_num} fix_distance_y {key}')

                    ax_dict[refs[2]].scatter(clean_times, clean_distInDeg, color='xkcd:light blue', s=20, alpha=0.2)
                    ax_dict[refs[2]].scatter(fix_times, fix_distInDeg, c=color_metric, cmap=cmap, s=20, alpha=1.0)
                    ax_dict[refs[2]].set_ylim(-0.1, 15)
                    ax_dict[refs[2]].set_xlim(0, run_dur)
                    ax_dict[refs[2]].set_title(f'{sub} {ses} {run_num} dist2center_deg {key}')

                fig.savefig(out_file)
                plt.close()

        except:
            print('could not process')


def main():
    '''
    This script performs drift-correction on the THINGS eyetracking data.

    If the argument "is_final" is false, it exports quality control figures
    to assess drift correction success.

    If "is_final" is true, it exports:
    - drift-corrected gaze data in bids-compliant format
    - enhanced *events.tsv files that included trial-wise fixation compliance
    metrics.

    Note: the *events.tsv files are based on the raw events files, not the
    ones with corrected behavioural metrics. To reconcile these files,
    run the things_add_etQC_2cleanEvents.py script, which imports fixation
    compliance metrics into the corrected events files (for final dataset).

    IMPORTANT: sub-06's ses-020 was ran under sub-01, is saved under sub-01's
    ses-020 (sep 15th is sub-06, sep 22nd is sub-01).
    Those output files (events, gaze and figures) needs to be RELABELLED
    MANUALLY to match the rest of the dataset (corrected events files, bold files).
    '''

    args = get_arguments()
    # e.g., (elm): /unf/eyetracker/neuromod/triplets/sourcedata
    in_path = args.in_path
    out_path = args.out_path

    is_final = args.is_final

    # load list of valid files (those deserialized and exported as npz in step 2 that passed QC)
    outpath_report = os.path.join(out_path, 'QC_gaze')
    Path(outpath_report).mkdir(parents=True, exist_ok=True)

    if is_final:
        file_list = pd.read_csv(f'{outpath_report}/QCed_finalbids_list.tsv', sep='\t', header=0)
    else:
        file_list = pd.read_csv(f'{outpath_report}/QCed_file_list.tsv', sep='\t', header=0)

    clean_list = file_list[file_list['DO_NOT_USE']!=1.0]
    if is_final:
        final_list = clean_list[clean_list['Fails_DriftCorr']!=1.0]
        clean_list = final_list

    clean_list['gaze_path'] = clean_list.apply(lambda row: create_gaze_path(row, out_path), axis=1)
    clean_list['events_path'] = clean_list.apply(lambda row: create_event_path(row, in_path), axis=1)
    clean_list['log_path'] = clean_list.apply(lambda row: create_event_path(row, in_path, log=True), axis=1)
    clean_list['infoplayer_path'] = clean_list.apply(lambda row: create_ip_path(row, in_path), axis=1)

    # implement drift correction on each run
    clean_list.apply(lambda row: driftCorr_ET(row, out_path, is_final), axis=1)


if __name__ == '__main__':
    sys.exit(main())
