#!/bin/bash
MAP=$1

getsym() {
	grep " $1 =" $MAP | awk '{ print $1 }'
}

FLASH_REMAIN=$(($(getsym _flash_top) - ($(getsym _edata) - $(getsym _sdata) + $(getsym _erodata))))
RAM_REMAIN=$(($(getsym _ram_top) - $(getsym _end)))
STATUS=0

if [[ $(getsym _ccm_top) ]]; then
  CCRAM_REMAIN=$(($(getsym _ccm_top) - $(getsym _eccmidata)))
  echo "$CCRAM_REMAIN bytes of CCRAM available."
  if (( CCRAM_REMAIN < 0 )); then
    STATUS=1
  fi
fi

echo "$RAM_REMAIN bytes of RAM available for heap."
echo "$FLASH_REMAIN bytes of flash unused."

if (( RAM_REMAIN < 0 || FLASH_REMAIN < 0 )); then
  STATUS=1
fi

exit $STATUS
