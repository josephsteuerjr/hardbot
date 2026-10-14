// The installed removal wizard gets its own temporary runtime before any user
// profile is opened or the application's instance lock is acquired.
if (process.argv.includes("--hard-uninstall") || process.argv.includes("--hard-uninstall-ready")) {
  require("../setup/main").startUninstaller();
} else require("./main");
