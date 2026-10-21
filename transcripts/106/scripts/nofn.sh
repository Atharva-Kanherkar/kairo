#!/bin/bash
cd /work
TP=hermes ./rig.sh start 3 > logs/rig-default.log 2>&1
./matrix.sh def 5 sigkill chat_tools:s4 messages_tools:s4 responses_tools:s4 > logs/nofn-default.txt 2>&1
export DYN_ENABLE_STRUCTURAL_TAG=false
TP=hermes ./rig.sh start 3 > logs/rig-nost.log 2>&1
./matrix.sh nost 5 sigkill chat_tools:s4 messages_tools:s4 responses_tools:s4 > logs/nofn-nost.txt 2>&1
echo NOFN_DONE >> logs/nofn-nost.txt
