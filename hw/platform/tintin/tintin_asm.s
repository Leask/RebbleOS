		.cpu cortex-m3
		.syntax unified
		.thumb

		.globl delay_us
		.type delay_us, %function
		.thumb_func
delay_us:
		mov r3, #6
		muls r0, r3
1:
		subs r0, #1
		bne 1b
		bx lr
		.size delay_us, . - delay_us
