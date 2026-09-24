{ lib, rustPlatform, pkg-config, openssl }:

rustPlatform.buildRustPackage {
  pname = "omarchy-srv";
  version = "0.1.0";

  src = lib.cleanSource ../src;

  cargoLock = {
    lockFile = ../src/Cargo.lock;
  };

  nativeBuildInputs = [ pkg-config ];
  buildInputs = [ openssl ];

  doCheck = true;

  meta = {
    description = "SUBSTRATE RAM-first runtime control-plane daemons";
    license = lib.licenses.asl20;
    platforms = lib.platforms.linux;
  };
}
