#!/bin/bash
set -e
echo "Building Python Lambda layer..."
rm -rf lambda/layer/python
mkdir -p lambda/layer/python
pip3 install anthropic boto3 -t lambda/layer/python --quiet
echo "Layer built at lambda/layer/python/"
