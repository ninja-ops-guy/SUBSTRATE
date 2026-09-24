{ config, lib, pkgs, ... }:

let
  cfg = config.services.substrate;
  servicePackage =
    if cfg.package == null
    then pkgs.runCommandNoCC "substrate-no-runtime-package" { } "mkdir -p $out/bin"
    else cfg.package;
in
{
  options.services.substrate = {
    enable = lib.mkEnableOption "SUBSTRATE host desired-state layer";

    package = lib.mkOption {
      type = lib.types.nullOr lib.types.package;
      default = null;
      description = ''
        Package containing omarchy-configd and omarchy-cgroupd. The NixOS base
        can be evaluated without it; production activation must provide a
        reproducibly built package.
      '';
    };

    profile = lib.mkOption {
      type = lib.types.enum [ "generic" "r720" ];
      default = "generic";
      description = "Qualified hardware policy profile.";
    };

    workspace = {
      enable = lib.mkOption {
        type = lib.types.bool;
        default = true;
        description = "Mount the RAM-first /ram workspace.";
      };

      size = lib.mkOption {
        type = lib.types.str;
        default = "96G";
        description = "tmpfs size used for the /ram workspace.";
      };
    };

    zram = {
      enable = lib.mkOption {
        type = lib.types.bool;
        default = true;
        description = "Enable compressed zram swap as the pressure buffer.";
      };

      memoryPercent = lib.mkOption {
        type = lib.types.ints.between 1 50;
        default = 25;
        description = "Maximum uncompressed zram capacity as a percentage of RAM.";
      };

      memoryMaxBytes = lib.mkOption {
        type = lib.types.nullOr lib.types.ints.positive;
        default = 68719476736;
        description = "Upper bound for zram capacity in bytes; defaults to 64 GiB.";
      };
    };
  };

  config = lib.mkIf cfg.enable {
    warnings = lib.optional (cfg.package == null)
      "SUBSTRATE NixOS base is enabled without a runtime package; omarchy daemons are intentionally not started.";

    assertions = [
      {
        assertion = cfg.profile != "r720" || cfg.workspace.enable;
        message = "The r720 SUBSTRATE profile requires the RAM-first /ram workspace.";
      }
    ];

    nix.settings.experimental-features = [ "nix-command" "flakes" ];
    nix.settings.auto-optimise-store = true;

    boot.kernel.sysctl."vm.swappiness" = lib.mkDefault 10;

    zramSwap = lib.mkIf cfg.zram.enable {
      enable = true;
      algorithm = "zstd";
      memoryPercent = cfg.zram.memoryPercent;
      memoryMax = cfg.zram.memoryMaxBytes;
    };

    fileSystems."/ram" = lib.mkIf cfg.workspace.enable {
      device = "tmpfs";
      fsType = "tmpfs";
      options = [
        "mode=0755"
        "size=${cfg.workspace.size}"
        "nodev"
        "nosuid"
      ];
    };

    environment.etc."omarchy-srv/ram.toml".source = ../../config/ram.toml;
    environment.etc."omarchy-srv/governor.toml".source = ../../config/governor.toml;
    environment.etc."omarchy-srv/numa-topology.toml".source = ../../config/numa-topology.toml;

    systemd.tmpfiles.rules = [
      "d /etc/omarchy-srv/live 0750 root root -"
      "d /etc/omarchy-srv/ring-buffer 0750 root root -"
      "d /run/omarchy-srv 0750 root root -"
      "d /run/omarchy-srv/staging 0750 root root -"
      "d /var/lib/substrate 0750 root root -"
      "d /var/lib/substrate/receipts 0750 root root -"
    ];

    systemd.services = lib.mkIf (cfg.package != null) {
      omarchy-configd = {
        description = "SUBSTRATE atomic runtime configuration daemon";
        after = [ "local-fs.target" ];
        wantedBy = [ "multi-user.target" ];

        serviceConfig = {
          Type = "simple";
          ExecStart = "${servicePackage}/bin/omarchy-configd";
          Restart = "always";
          RestartSec = "500ms";
          OOMScoreAdjust = -1000;
          MemoryMin = "128M";
          MemoryMax = "512M";
          ProtectSystem = "strict";
          ProtectHome = true;
          ReadWritePaths = [
            "/etc/omarchy-srv/live"
            "/etc/omarchy-srv/ring-buffer"
            "/run/omarchy-srv"
          ];
          PrivateTmp = true;
          NoNewPrivileges = true;
          LockPersonality = true;
          RestrictSUIDSGID = true;
        };
      };

      omarchy-cgroupd = {
        description = "SUBSTRATE cgroup v2 hardware constraint engine";
        after = [ "local-fs.target" "omarchy-configd.service" ];
        requires = [ "omarchy-configd.service" ];
        wantedBy = [ "multi-user.target" ];

        serviceConfig = {
          Type = "simple";
          ExecStart = "${servicePackage}/bin/omarchy-cgroupd";
          Restart = "always";
          RestartSec = "500ms";
          OOMScoreAdjust = -1000;
          CPUSchedulingPolicy = "fifo";
          CPUSchedulingPriority = 50;
          MemoryMin = "256M";
          MemoryMax = "1G";
          MemoryOOMGroup = true;
          CPUQuota = "50%";
          CapabilityBoundingSet = [
            "CAP_SYS_RESOURCE"
            "CAP_DAC_OVERRIDE"
            "CAP_SYS_NICE"
          ];
          ProtectSystem = "strict";
          ProtectHome = true;
          ReadWritePaths = [ "/sys/fs/cgroup" ];
          PrivateTmp = true;
          NoNewPrivileges = true;
          LockPersonality = true;
          RestrictSUIDSGID = true;
        };
      };
    };
  };
}
