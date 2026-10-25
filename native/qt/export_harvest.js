        function harvestPage() {
            // The C++ cursor owns model positions and changes. It does not
            // interpret an empty local queue as the end of server history.
            const lease = historyCursor.reserve(limits.maxPageMessages);
            if (!lease || lease.ok !== true) throw new Error("history_cursor");
            if (!lease.contexts.length) return false;
            const ownerJob = jobId, ownerToken = jobToken;
            let records = [], bytes = 512, pageAttachments = 0, consumed = 0;
            const started = Date.now();
            try {
                for (let i = 0; i < lease.contexts.length; ++i) {
                    // Yield between messages; native MAX objects stay on the
                    // GUI thread and no single SDK call is preempted.
                    if (i > 0 && Date.now() - started >= 2) break;
                    const context = lease.contexts[i];
                    const nativeMessage = getContext(context);
                    // A wrapper can exist before its message is ready. Keep
                    // that model position for a later turn, even if no model
                    // role replacement signal accompanies initialization.
                    if (!nativeMessage) break;
                    const key = nativeMessage ? canonicalId(nativeMessage.messageId, "message") : "";
                    if (nativeMessage && (nativeMessage.isPending === true || nativeMessage.isDelayed === true)) {
                        if (key) sentIds["pending:" + key] = true;
                        consumed += 1; continue;
                    }
                    if (!key || sentIds[key]) { consumed += 1; continue; }
                    if (messageCount + records.length >= limits.maxMessages) { stopReason = "message_limit"; break; }
                    const dto = messageDTO(context);
                    if (!dto) break;
                    prepareAttachments(context, dto);
                    if (attachmentCount + pageAttachments + dto.attachments.length > limits.maxAttachments) { stopReason = "attachment_limit"; break; }
                    const size = utf8Length(JSON.stringify(dto)) + 1;
                    if (size + 512 > limits.maxPageBytes) { stopReason = "message_too_large"; break; }
                    if (records.length > 0 && bytes + size > limits.maxPageBytes - 1024) break;
                    records.push(dto); bytes += size; pageAttachments += dto.attachments.length;
                    consumed += 1;
                }
                // Only acknowledge the processed prefix. A size/time boundary
                // returns every remaining model index to the native cursor.
                if (!historyCursor.commit(lease.ticket, consumed)
                        || historyCursor.generation !== lease.generation
                        || jobId !== ownerJob || jobToken !== ownerToken || phase !== "history")
                    throw new Error("history_cursor_changed");
            } catch (error) {
                historyCursor.release(lease.ticket);
                throw error;
            }
            if (records.length === 0) {
                // A duplicate-only batch still counts as work. Let the next
                // timer turn drain it before requesting another history page.
                return consumed > 0 && !stopReason && historyCursor.pendingCount > 0;
            }
            pendingMessages = records; phase = "page";
            request("page", jobBody({sequence: sequence, messages: records}), function(value) {
                if (value.sequence !== sequence || !Array.isArray(value.media)) throw new Error("page");
                for (const dto of records) { sentIds[dto.id] = true; messageCount += 1; attachmentCount += dto.attachments.length; for (const a of dto.attachments) if (a.status === "unavailable") omissionCount += 1; }
                sequence += 1; pendingMessages = []; mediaQueue = value.media.slice(); phase = "media";
                statusText = "Сохранено сообщений: " + messageCount + ". Загрузка вложений…";
                nextMedia();
            });
            return true;
        }
