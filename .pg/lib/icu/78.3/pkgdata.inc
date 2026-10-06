GENCCODE_ASSEMBLY_TYPE=-a gcc
SO=so
SOBJ=so
A=a
LIBPREFIX=lib
LIB_EXT_ORDER=.78.3
COMPILE=aarch64-conda-linux-gnu-cc -DNDEBUG -D_FORTIFY_SOURCE=2 -O2 -isystem /workspace/.pg/include -D_REENTRANT  -DU_HAVE_ELF_H=1  -DU_ALL_IMPLEMENTATION -DU_ATTRIBUTE_DEPRECATED= -ftree-vectorize -fPIC -fstack-protector-strong -fno-plt -O3 -pipe -fno-merge-constants -isystem /workspace/.pg/include -fdebug-prefix-map=/home/conda/feedstock_root/build_artifacts/bld/rattler-build_icu_1786545779/work=/usr/local/src/conda/icu-78.3 -fdebug-prefix-map=/workspace/.pg=/usr/local/src/conda-prefix -std=c11 -Wall -pedantic -Wshadow -Wpointer-arith -Wmissing-prototypes -Wwrite-strings   -c
LIBFLAGS=-I/workspace/.pg/include -DPIC -fPIC
GENLIB=aarch64-conda-linux-gnu-cc -ftree-vectorize -fPIC -fstack-protector-strong -fno-plt -O3 -pipe -fno-merge-constants -isystem /workspace/.pg/include -fdebug-prefix-map=/home/conda/feedstock_root/build_artifacts/bld/rattler-build_icu_1786545779/work=/usr/local/src/conda/icu-78.3 -fdebug-prefix-map=/workspace/.pg=/usr/local/src/conda-prefix -std=c11 -Wall -pedantic -Wshadow -Wpointer-arith -Wmissing-prototypes -Wwrite-strings   -Wl,-O2 -Wl,--sort-common -Wl,--as-needed -Wl,-z,relro -Wl,-z,now -Wl,--allow-shlib-undefined -Wl,-rpath,/workspace/.pg/lib -Wl,-rpath-link,/workspace/.pg/lib -L/workspace/.pg/lib  -shared -Wl,-Bsymbolic
LDICUDTFLAGS=-nodefaultlibs -nostdlib
LD_SONAME=-Wl,-soname -Wl,
RPATH_FLAGS=
BIR_LDFLAGS=-Wl,-Bsymbolic
AR=aarch64-conda-linux-gnu-ar
ARFLAGS=r
RANLIB=aarch64-conda-linux-gnu-ranlib
INSTALL_CMD=/usr/bin/install -c
