{
  description = "SUBSTRATE — NixOS desired-state layer for RAM-first inference hosts";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-26.05";
  };

  outputs = { self, nixpkgs }:
    let
      system = "x86_64-linux";
      pkgs = nixpkgs.legacyPackages.${system};
      substratePackage = pkgs.callPackage ./nix/package.nix { };
    in
    {
      nixosModules.substrate = import ./nix/modules/substrate.nix;
      nixosModules.r720 = import ./nix/hardware/r720.nix;

      nixosConfigurations.substrate-r720-eval = nixpkgs.lib.nixosSystem {
        inherit system;
        modules = [
          self.nixosModules.substrate
          self.nixosModules.r720
          ./nix/hosts/r720.nix
          ({ ... }: {
            # CI/reference evaluation only. A physical R720 must supply its
            # generated root filesystem and boot-loader configuration.
            boot.isContainer = true;
            fileSystems."/" = {
              device = "none";
              fsType = "tmpfs";
            };
          })
        ];
      };

      nixosConfigurations.substrate-installer = nixpkgs.lib.nixosSystem {
        inherit system;
        modules = [
          ./nix/images/installer.nix
        ];
      };

      packages.${system} = {
        default = substratePackage;
        substrate = substratePackage;
        installer =
          self.nixosConfigurations.substrate-installer.config.system.build.isoImage;
      };

      checks.${system} = {
        substrate-package = substratePackage;
        r720-system =
          self.nixosConfigurations.substrate-r720-eval.config.system.build.toplevel;
      };
    };
}
