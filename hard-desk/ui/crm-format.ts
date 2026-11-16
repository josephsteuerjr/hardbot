/** Format canonical decimal strings without floating-point rounding. */
export function decimalText(value:unknown):string {
  const text=String(value ?? '');
  if(!/^-?\d{1,15}(\.\d{1,2})?$/.test(text))return text;
  const [whole,fraction='']=text.split('.');
  const decimals=fraction.replace(/0+$/,'');
  return whole.replace(/\B(?=(\d{3})+(?!\d))/g,'\u00a0')+(decimals?','+decimals:'');
}
