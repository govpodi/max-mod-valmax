option casemap:none
EXTERN OwnedHistoryMessages:PROC
PUBLIC OwnedHistoryCall
PUBLIC OwnedHistoryReturn
.code
OwnedHistoryCall PROC
    push rdi
    sub rsp,20h
    mov rdi,QWORD PTR [rsp+50h]
    call OwnedHistoryMessages
OwnedHistoryReturn LABEL BYTE
    add rsp,20h
    pop rdi
    ret
OwnedHistoryCall ENDP
END
