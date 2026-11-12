"""Experimental v8 wire projection AFTER logical output pacing.

Native effects, receipts, bindings and deadlines use original logical frames.
No buffering, acknowledgements, retries or timing decisions live here.
"""
SNAPSHOT=(0,1,2,4,5,6,7,8,9,10,11,12,15,16,17)

def _frame(status,start,revision,body):
    return [(status,start,revision)]+[(status,40+i,v) for i,v in enumerate(body)]+[(status,40+len(body),revision)]

def project_wire(messages):
    messages=list(messages)
    if not messages:return messages
    status,start,revision=messages[0]
    if len(messages)==20 and 0xB0<=status<=0xB7 and start==40:
        body=[m[2] for m in messages[1:-1]]
        return _frame(status,33,revision,[body[i] for i in SNAPSHOT])
    if len(messages)==20 and 0xB0<=status<=0xBF and start==70:
        return _frame(status,34,revision,[m[2] for m in messages[1:-1]])
    if len(messages)==9 and status==0xBF and start==20:
        return _frame(status,35,revision,[m[2] for m in messages[1:-1]])
    return messages
