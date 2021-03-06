#!/usr/bin/env bash
CONTR_ID=$(fuel node | grep controller | head -1 | awk '{print$1}')
scp -rp ./../../mos_tests/ node-$CONTR_ID:~/

ssh node-$CONTR_ID "export PYTHONPATH=.:$PYTHONPATH && source ~/openrc && nosetests mos_tests/cinder/cinder_tests.py --with-xunit --xunit-file=cinder_tests_report.xml"
scp node-$CONTR_ID:~/cinder_tests_report.xml ~/

# CleanUp
ssh node-$CONTR_ID "\rm -rf ~/mos_tests"

# Show command to view results
echo -e "For test results see:\n\t# less ~/cinder_tests_report.xml\n\t# xmllint --format ~/cinder_tests_report.xml"
