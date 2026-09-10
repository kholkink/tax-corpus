#!/usr/bin/env bash
# Планировщик для контейнера (P7): daily в 03:00, бэкап в 03:30, оценка и карта точности по понедельникам.
set -u
cd "$(dirname "$0")/.."
python -m taxcorpus jobs run snapshot >> reports/jobs.log 2>&1 || true
while true; do
  now=$(date +%H:%M); dow=$(date +%u)
  case "$now" in
    03:00) python -m taxcorpus jobs run daily >> reports/jobs.log 2>&1 ;;
    03:30) bash scripts/backup.sh >> reports/jobs.log 2>&1 ;;
    04:00) [ "$dow" = "1" ] && python -m taxcorpus jobs run eval_agent -- --limit 30 >> reports/jobs.log 2>&1 ;;
    05:00) [ "$dow" = "7" ] && python -m taxcorpus jobs run extract_positions -- --limit 200 >> reports/jobs.log 2>&1 ;;
    05:30) [ "$dow" = "1" ] && python -m taxcorpus jobs run eval_search >> reports/jobs.log 2>&1 ;;
    05:45) [ "$dow" = "1" ] && python -m taxcorpus jobs run accuracy >> reports/jobs.log 2>&1 ;;
  esac
  sleep 60
done
