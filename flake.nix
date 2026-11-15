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

          LD_LIBRARY_PATH = pkgs.lib.makeLibraryPath [
            pkgs.stdenv.cc.cc.lib
            pkgs.zlib
            # rasterio's GDAL wheel needs libexpat; same nixpkgs as python3, so
            # it matches the expat python's own pyexpat was built against
            pkgs.expat
          ];
        };
      }
    );
}
