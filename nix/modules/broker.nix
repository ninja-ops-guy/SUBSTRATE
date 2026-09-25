{ config, lib, pkgs, ... }:
let
  cfg = config.services.substrate.broker;
  policyFile = pkgs.writeText "substrate-broker-policy.json" (builtins.toJSON {
    schema = 1;
    allowed_uids = cfg.allowedUIDs;
    read_operations = cfg.readOperations;
    service_units = cfg.serviceUnits;
    restart_units = cfg.restartUnits;
    enable_service_restart = cfg.enableServiceRestart;
    task_ttl_seconds = cfg.taskTTLSeconds;
    approval_ttl_seconds = cfg.approvalTTLSeconds;
    max_calls = cfg.maxCalls;
  });
in
{
  options.services.substrate.broker = {
    enable = lib.mkEnableOption "SUBSTRATE task-scoped OS broker";
    package = lib.mkOption {
      type = lib.types.package;
      default = pkgs.callPackage ../broker-package.nix { };
      description = "Broker package; independent of the existing Rust daemons.";
    };
    allowedUIDs = lib.mkOption {
      type = lib.types.listOf lib.types.ints.positive;
      default = [ ];
      description = "Explicitly enrolled non-root worker UIDs (also require socket group membership).";
    };
    readOperations = lib.mkOption {
      type = lib.types.listOf (lib.types.enum [ "system.identify" "memory.snapshot" "service.status" ]);
      default = [ "system.identify" "memory.snapshot" ];
    };
    serviceUnits = lib.mkOption {
      type = lib.types.listOf lib.types.str;
      default = [ ];
      description = "Exact, non-template service names available for status queries.";
    };
    restartUnits = lib.mkOption {
      type = lib.types.listOf lib.types.str;
      default = [ ];
      description = "Subset of serviceUnits eligible for per-invocation administrator approval.";
    };
    enableServiceRestart = lib.mkOption {
      type = lib.types.bool;
      default = false;
      description = "Opt in to the narrow restart adapter; never authorizes a restart by itself.";
    };
    taskTTLSeconds = lib.mkOption {
      type = lib.types.ints.between 1 3600;
      default = 300;
    };
    approvalTTLSeconds = lib.mkOption {
      type = lib.types.ints.between 1 120;
      default = 30;
    };
    maxCalls = lib.mkOption {
      type = lib.types.ints.between 1 128;
      default = 32;
    };
  };

  config = lib.mkIf cfg.enable {
    assertions = [
      {
        assertion = lib.all (unit: builtins.elem unit cfg.serviceUnits) cfg.restartUnits;
        message = "SUBSTRATE broker restartUnits must be a subset of serviceUnits.";
      }
    ];
    users.groups.substrate-broker = { };
    environment.systemPackages = [ cfg.package ];
    environment.etc."substrate/broker-policy.json".source = policyFile;
    systemd.services.substrate-broker = {
      description = "SUBSTRATE task authority and audited OS broker";
      wantedBy = [ "multi-user.target" ];
      after = [ "local-fs.target" "dbus.service" ];
      serviceConfig = {
        Type = "simple";
        User = "root";
        Group = "substrate-broker";
        ExecStart = "${cfg.package}/bin/substrate-brokerd --policy ${policyFile} --catalog ${cfg.package}/share/substrate-broker/os_operations.json --systemctl ${pkgs.systemd}/bin/systemctl";
        Restart = "on-failure";
        RestartSec = "2s";
        RuntimeDirectory = "substrate-broker";
        RuntimeDirectoryMode = "0750";
        StateDirectory = "substrate-broker";
        StateDirectoryMode = "0700";
        UMask = "0077";
        NoNewPrivileges = true;
        CapabilityBoundingSet = "";
        ProtectSystem = "strict";
        ProtectHome = true;
        PrivateTmp = true;
        PrivateDevices = true;
        ProtectKernelTunables = true;
        ProtectKernelModules = true;
        ProtectKernelLogs = true;
        ProtectControlGroups = true;
        ProtectHostname = true;
        RestrictAddressFamilies = [ "AF_UNIX" ];
        RestrictNamespaces = true;
        RestrictSUIDSGID = true;
        LockPersonality = true;
        MemoryDenyWriteExecute = true;
        ReadWritePaths = [ "/var/lib/substrate-broker" "/run/substrate-broker" ];
        MemoryMax = "256M";
        TasksMax = 32;
        LimitNOFILE = 128;
        TimeoutStopSec = "15s";
        KillMode = "control-group";
      };
    };
  };
}
