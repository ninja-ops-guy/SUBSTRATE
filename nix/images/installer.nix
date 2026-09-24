{ modulesPath, pkgs, ... }:

{
  imports = [
    "${modulesPath}/installer/cd-dvd/installation-cd-minimal.nix"
    ../modules/substrate.nix
    ../hardware/r720.nix
  ];

  networking.hostName = "substrate-installer";

  services.substrate = {
    enable = true;
    profile = "r720";
    package = null;
    zram.enable = false;
    workspace.enable = true;
    workspace.size = "16G";
  };

  environment.systemPackages = with pkgs; [
    btrfs-progs
    cryptsetup
    git
    jq
  ];

  system.stateVersion = "26.05";
}
