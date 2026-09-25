{ lib, stdenvNoCC, python3, makeWrapper }:

stdenvNoCC.mkDerivation {
  pname = "substrate-broker";
  version = "0.1.0";
  src = lib.cleanSource ../.;
  nativeBuildInputs = [ python3 makeWrapper ];
  dontBuild = true;
  doCheck = true;
  checkPhase = ''
    runHook preCheck
    export PYTHONDONTWRITEBYTECODE=1
    PYTHONPATH=broker ${python3}/bin/python3 -m unittest discover -s broker/tests -v
    runHook postCheck
  '';
  installPhase = ''
    runHook preInstall
    mkdir -p $out/lib/substrate-broker $out/share/substrate-broker $out/bin
    cp broker/*.py $out/lib/substrate-broker/
    cp src/os_operations.json $out/share/substrate-broker/
    makeWrapper ${python3}/bin/python3 $out/bin/substrate-brokerd \
      --add-flags "-E -s -B $out/lib/substrate-broker/main.py serve"
    makeWrapper ${python3}/bin/python3 $out/bin/substrate-osctl \
      --add-flags "-E -s -B $out/lib/substrate-broker/main.py request"
    runHook postInstall
  '';
  meta = {
    description = "SUBSTRATE task-scoped local OS broker (opt-in, default read-only)";
    license = lib.licenses.asl20;
    platforms = lib.platforms.linux;
  };
}
