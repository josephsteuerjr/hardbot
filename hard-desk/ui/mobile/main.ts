import {bridge} from './bridge';
import {mobileShell} from './shell';
(window as any).crm=bridge;
mobileShell();
void import('../crm-app');
