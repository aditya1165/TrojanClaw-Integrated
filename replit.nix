{ pkgs }: {
  deps = [ pkgs.git pkgs.stdenv.cc.cc.lib ];
  # The Playwright/Patchright Node driver needs the C++ runtime even though
  # Chromium itself runs remotely in Browserbase.
  env.LD_LIBRARY_PATH = pkgs.lib.makeLibraryPath [ pkgs.stdenv.cc.cc.lib ];
}
