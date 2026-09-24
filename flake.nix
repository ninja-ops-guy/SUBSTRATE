{
  description = "SUBSTRATE — NixOS desired-state layer for RAM-first inference hosts";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-26.05";
  };

  outputs = { self, nixpkgs }:
    let
      system = "x86_64-linux";
    in
    {
      nixosModules.substrate = import ./nix/modules/substrate.nix;
      nixosModules.r720 = import ./nix/hardware/r720.nix;

      nixosConfigurations.substrate-r720 = nixpkgs.lib.nixosSystem {
        inherit system;
        modules = [
          self.nixosModules.substrate
          self.nixosModules.r720
          ./nix/hosts/r720.nix
        ];
      };

      nixosConfigurations.substrate-installer = nixpkgs.lib.nixosSystem {
        inherit system;
        modules = [
          ./nix/images/installer.nix
        ];
      };

      packages.${system}.installer =
        self.nixosConfigurations.substrate-installer.config.system.build.isoImage;

      checks.${system}.r720-system =
        self.nixosConfigurations.substrate-r720.config.system.build.toplevel;
    };
}
