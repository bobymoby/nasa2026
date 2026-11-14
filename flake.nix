{
  inputs.nixpkgs.url = "github:NixOS/nixpkgs/master";
  inputs.flake-utils.url = "github:numtide/flake-utils";

  outputs =
    {
      self,
      nixpkgs,
      flake-utils,
    }:
    flake-utils.lib.eachDefaultSystem (
      system:
      let
        pkgs = nixpkgs.legacyPackages.${system};
        phpEnv = pkgs.php.buildEnv {
          extensions = { enabled, all }: enabled;
          # extraConfig = "memory_limit=-1";
        };
      in
      {
        devShell = pkgs.mkShell {
          nativeBuildInputs = [ pkgs.bashInteractive ];
          buildInputs = with pkgs; [
		nodejs
          	python3
	  ];
        };
      }
    );
}
