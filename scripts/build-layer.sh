#!/bin/bash
set -e
rm -rf lambda/layer/python
mkdir -p lambda/layer/python
# boto3 is included in Lambda Python runtime — no extra packages needed
# Placeholder file required so CDK can produce a non-empty zip
echo "# boto3 available in Lambda Python runtime" > lambda/layer/python/README.txt
echo "Layer directory ready (boto3 available in Lambda runtime)"
