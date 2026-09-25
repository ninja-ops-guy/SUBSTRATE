{ pkgs }:
let
  brokerPackage = pkgs.callPackage ../broker-package.nix { };
in
pkgs.testers.runNixOSTest {
  name = "substrate-broker-task-authority";
  nodes.machine = { ... }: {
    imports = [ ../modules/broker.nix ];
    users.users.worker = {
      isNormalUser = true;
      uid = 1000;
      extraGroups = [ "substrate-broker" ];
    };
    services.substrate.broker = {
      enable = true;
      allowedUIDs = [ 1000 ];
      readOperations = [ "system.identify" "memory.snapshot" "service.status" ];
      serviceUnits = [ "demo.service" ];
      restartUnits = [ "demo.service" ];
      enableServiceRestart = true;
    };
    systemd.services.demo = {
      wantedBy = [ "multi-user.target" ];
      serviceConfig = {
        Type = "oneshot";
        RemainAfterExit = true;
        StateDirectory = "broker-demo";
      };
      script = ''
        count=0
        test ! -f /var/lib/broker-demo/count || count=$(cat /var/lib/broker-demo/count)
        echo $((count + 1)) > /var/lib/broker-demo/count
      '';
    };
    environment.systemPackages = [ pkgs.python3 pkgs.util-linux ];
    environment.etc."broker-smoke.py".text = ''
      CLIENT = "${brokerPackage}/bin/substrate-osctl"
    '' + builtins.readFile ./broker-smoke.py;
    system.stateVersion = "26.05";
  };
  testScript = ''
    start_all()
    machine.wait_for_unit("substrate-broker.service")
    machine.wait_for_unit("demo.service")
    machine.wait_until_succeeds("test -S /run/substrate-broker/agent.sock")
    machine.succeed("python3 /etc/broker-smoke.py")
    machine.fail("runuser -u worker -- cat /var/lib/substrate-broker/broker.sqlite3")
    machine.fail("runuser -u worker -- systemctl --no-ask-password restart demo.service")
  '';
}
