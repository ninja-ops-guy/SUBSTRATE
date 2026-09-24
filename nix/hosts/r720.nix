{ lib, ... }:

{
  networking.hostName = lib.mkDefault "substrate-r720";

  services.substrate = {
    enable = true;
    profile = "r720";

    workspace = {
      enable = true;
      size = "96G";
    };

    zram = {
      enable = true;
      memoryPercent = 25;
      memoryMaxBytes = 68719476736;
    };
  };

  # Device-specific filesystems, boot loader selection, encryption, and PERC
  # layout belong in the generated host hardware configuration and must be
  # bound to hardware qualification evidence before production use.
  system.stateVersion = "26.05";
}
