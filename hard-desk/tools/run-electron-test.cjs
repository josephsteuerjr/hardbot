const { spawn } = require('node:child_process');
const path = require('node:path');
const env = { ...process.env }; delete env.ELECTRON_RUN_AS_NODE;
const p = spawn(path.join(__dirname, '../node_modules/electron/dist/electron.exe'), process.argv.slice(2), { windowsHide: true, stdio: 'inherit', env });
p.on('exit', code => process.exit(typeof code === 'number' ? code : 1));
p.on('error', error => { console.error(error); process.exit(1); });
