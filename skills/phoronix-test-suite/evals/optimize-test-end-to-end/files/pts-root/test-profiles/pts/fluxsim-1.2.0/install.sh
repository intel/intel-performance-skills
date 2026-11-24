#!/bin/sh
tar -xzf fluxsim-1.2.tar.gz
cd fluxsim
make -j $NUM_CPU_CORES CFLAGS="-O2 $CFLAGS"
echo $? > ~/install-exit-status
cp fluxsim-bin ..
cd ..
rm -rf fluxsim
echo "#!/bin/sh
./fluxsim-bin --iterations 1500 \$@ > \$LOG_FILE 2>&1
echo \$? > ~/test-exit-status" > fluxsim
chmod +x fluxsim
