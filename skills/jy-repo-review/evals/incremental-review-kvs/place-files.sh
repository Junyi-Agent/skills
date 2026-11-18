#!/usr/bin/env bash
set -eu
bash "$(dirname "$0")/../fixture/build_kvs.sh" incremental
