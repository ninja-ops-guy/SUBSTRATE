{ lib, pkgs, ... }:

{
  nixpkgs.hostPlatform = lib.mkDefault "x86_64-linux";

  # Keep this profile conservative: exact controller, NUMA, firmware, and
  # performance policy remain qualification outputs, not assumptions.
  boot.initrd.availableKernelModules = [
    "ahci"
    "megaraid_sas"
    "mpt3sas"
    "sd_mod"
    "xhci_pci"
  ];

  hardware.cpu.intel.updateMicrocode = lib.mkDefault true;

  environment.systemPackages = with pkgs; [
    btrfs-progs
    dmidecode
    hwloc
    ipmitool
    numactl
    pciutils
    smartmontools
  ];

  services.smartd.enable = lib.mkDefault true;
}
