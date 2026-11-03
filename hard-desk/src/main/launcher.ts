// The installed removal wizard gets its own temporary runtime before any user
// profile is opened or the application's instance lock is acquired.
if (process.argv.includes("--hard-uninstall") || process.argv.includes("--hard-uninstall-ready")) {
  require("../setup/main").startUninstaller();
} else {
  const {PRODUCT_ID}=require('./product');
  if(PRODUCT_ID==='crm')require('./crm-pilot');else require("./main");
}
