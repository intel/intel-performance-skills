#!/bin/bash
# verify.py writes /logs/verifier/reward.json; a crash before that leaves no reward,
# which Harbor reports as an error rather than a 0.
python3 /tests/verify.py
