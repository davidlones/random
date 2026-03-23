#!/usr/bin/env python
# _0853RV3R

def on_launch(event, context):
	return statement('Greetings!', 'system connected')


def lambda_handler(event, context):
	if event['request']['type'] == 'LaunchRequest':
		return on_launch(event, context)


