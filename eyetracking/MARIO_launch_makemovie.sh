#!/bin/bash -l

#module load python/3.7 #?? on elm?

#source /home/labopb/Documents/Marie/neuromod/pupil_venv/bin/activate

EYETRACKDIR="/home/labopb/Documents/Marie/neuromod/Mario/Eye-tracking/pupil_data/sub-01/ses-002"
GAZEDIR="/home/labopb/Documents/Marie/neuromod/Mario/Eye-tracking/offline_calibration/sub-01/ses-002"
OUTDIR="/home/labopb/Documents/Marie/neuromod/Mario/tests2"

python -m MARIO_makemovie \
      --file_path="${EYETRACKDIR}" \
      --gaze_path="${GAZEDIR}" \
      --out_path="${OUTDIR}" \
      --conf=0.80
