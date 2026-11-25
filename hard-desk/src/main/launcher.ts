// The installed removal wizard gets its own temporary runtime before any user
// profile is opened or the application's instance lock is acquired.
if (process.argv.includes("--hard-uninstall") || process.argv.includes("--hard-uninstall-ready")) {
  require("../setup/main").startUninstaller();
} else {
  const {PRODUCT_ID,STABLE_CRM}=require('./product');
  if(PRODUCT_ID==='crm'||STABLE_CRM){
    if(process.argv.includes('--crm-diagnose'))void require('./crm-startup-recovery').showStartupRecovery('manual_startup_diagnostics');
    else try{require('./crm-pilot');}catch{void require('./crm-startup-recovery').showStartupRecovery('main_unavailable');}
  }else require("./main");
}
