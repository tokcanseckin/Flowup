#!/bin/zsh
source .venv/bin/activate
nohup python3 -m pipeline.onboard_playlist \
  --csv content_to_add/songs/beginner_english_playlist.csv \
  --lang en \
  --api-url https://singoling.com \
  --playlist-id 6 \
  --start-at 18 \
  --admin-token "1.2acd3c7384df01607bd3deb237f1995316edfcab01ad1483d3d8e7af21929275" \
  > pipeline_run.log 2>&1 &
echo "Pipeline started. PID: $BGPID  — tail -f pipeline_run.log to follow"
