"""Minimal lossless protobuf wire reader for isolated protocol diagnostics."""
import base64
import json
import re

def varint(data,pos):
    value=0
    for shift in range(0,70,7):
        if pos>=len(data):raise ValueError('truncated')
        b=data[pos];pos+=1;value|=(b&127)<<shift
        if b<128:return value,pos
    raise ValueError('invalid varint')

def fields(data):
    out=[];pos=0
    while pos<len(data):
        tag,pos=varint(data,pos);number,kind=tag>>3,tag&7
        if not number:raise ValueError('zero tag')
        if kind==0:value,pos=varint(data,pos)
        elif kind in (1,5):
            size=8 if kind==1 else 4;value=data[pos:pos+size];pos+=size
            if len(value)!=size:raise ValueError('truncated fixed')
        elif kind==2:
            size,pos=varint(data,pos);value=data[pos:pos+size];pos+=size
            if len(value)!=size:raise ValueError('truncated data')
        else:raise ValueError('unsupported wire')
        out.append((number,kind,value))
    return out
