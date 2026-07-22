	// --- BEGIN openclaw label-probe (read-only) ---
	try {
		const __ocLabelDir = __ocHomedir() + "/.openclaw";
		let __ocLabels = {};
		try { __ocLabels = JSON.parse(__ocReadFile(__ocLabelDir + "/whatsapp-labels.json", "utf8")) || {}; } catch {}
		attachEmitterListener(sock.ev, "labels.edit", (label) => {
			try {
				__ocLabels[label.id] = { id: label.id, name: label.name, color: label.color, deleted: !!label.deleted, predefinedId: label.predefinedId ?? null };
				__ocWriteFile(__ocLabelDir + "/whatsapp-labels.json", JSON.stringify(__ocLabels, null, 2));
				inboundLogger.info({ labelId: label.id, name: label.name }, "[label-probe] discovered label");
			} catch (e) { try { inboundLogger.warn({ error: String(e) }, "[label-probe] write failed"); } catch {} }
		});
		const __ocProbeStartedAt = Date.now();
		attachEmitterListener(sock.ev, "labels.association", (assoc) => {
			try { __ocAppendFile(__ocLabelDir + "/whatsapp-label-assoc.log", JSON.stringify({ ts: new Date().toISOString(), ...assoc }) + "\n"); } catch {}
			try {
				const eventType = String(assoc?.type || "").toLowerCase();
				const relation = assoc?.association || {};
				const chatId = String(relation.chatId || "");
				const labelId = String(relation.labelId || "");
				// Track the chat's ACTUAL current labels; the reconciler uses this to strip extras.
				const __ocTrack = globalThis[Symbol.for("openclaw.wa.chatLabels")] || (globalThis[Symbol.for("openclaw.wa.chatLabels")] = new Map());
				const __ocSet = __ocTrack.get(chatId) || new Set();
				if (eventType === "add") __ocSet.add(labelId); else if (eventType === "remove") __ocSet.delete(labelId);
				__ocTrack.set(chatId, __ocSet);
				// Echo suppression: events caused by our own reconciler ops must not re-enter the DB mirror.
				const __ocOps = globalThis[Symbol.for("openclaw.wa.labelOps")] || (globalThis[Symbol.for("openclaw.wa.labelOps")] = new Map());
				const __ocOpKey = eventType + ":" + chatId + ":" + labelId;
				if (__ocOps.has(__ocOpKey)) { __ocOps.delete(__ocOpKey); return; }
				// Replay guard: association replays right after connect are history, not user actions.
				// The SQLite category is the source of truth on restart.
				if (Date.now() - __ocProbeStartedAt < 90000) return;
				const lid = chatId.endsWith("@lid") ? chatId.slice(0, -4) : "";
				let phone = "";
				if (lid) {
					try { phone = String(JSON.parse(__ocReadFile(__ocLabelDir + "/credentials/whatsapp/default/lid-mapping-" + lid + "_reverse.json", "utf8")) || ""); } catch {}
				}
				if (!phone && chatId.endsWith("@s.whatsapp.net")) phone = chatId.split("@")[0];
				let labelName = "";
				for (const lab of Object.values(__ocLabels)) {
					if (lab && String(lab.id) === labelId && !lab.deleted) labelName = String(lab.name || "").trim().toLowerCase();
				}
				if (labelName === "complains") labelName = "complaints";
				const managed = ["new customer", "important", "hot leads", "followup", "junk", "complaints", "ahsan", "ahmed", "imran", "rafay"];
				if (phone && managed.includes(labelName)) {
					const ws = (() => { try { return loadConfig()?.agents?.defaults?.workspace; } catch { return null; } })() || (__ocHomedir() + "/wa-lead-gen/workspace");
					const dbScript = ws.replace(/\/$/, "") + "/db.py";
					const customersPath = ws.replace(/\/$/, "") + "/data/customers.json";
					let current = "";
					try {
						const data = JSON.parse(__ocReadFile(customersPath, "utf8"));
						const row = (data.customers || []).find((c) => String(c.phone || "").replace(/\D/g, "") === phone.replace(/\D/g, ""));
						current = String(row?.category || "").trim().toLowerCase();
					} catch {}
					let next = null;
					if (eventType === "add") next = labelName;
					if (eventType === "remove" && current === labelName) next = "new customer";
					if (next && next !== current) {
						__ocExecFile("/usr/bin/python3", [dbScript, "set-category", "--phone", "+" + phone.replace(/\D/g, ""), "--category", next], { timeout: 10000 }, (error, stdout, stderr) => {
							if (error) inboundLogger.warn({ error: String(error), stderr }, "[label-sync] database update failed");
							else inboundLogger.info({ phone, oldCategory: current, newCategory: next }, "[label-sync] database updated from WhatsApp");
						});
					}
				}
			} catch (e) { try { inboundLogger.warn({ error: String(e) }, "[label-sync] event failed"); } catch {} }
		});
		setTimeout(() => {
			inboundLogger.info({ hasResync: typeof sock.resyncAppState, hasAddChatLabel: typeof sock.addChatLabel }, "[label-probe] socket caps");
			(async () => {
				try {
					if (typeof sock.resyncAppState === "function") {
						await sock.resyncAppState(["critical_block", "critical_unblock_low", "regular_high", "regular"], true);
						inboundLogger.info({}, "[label-probe] resyncAppState done");
					} else inboundLogger.warn({}, "[label-probe] resyncAppState missing");
				} catch (e) { try { inboundLogger.warn({ error: String(e) }, "[label-probe] resync failed"); } catch {} }
			})();
		}, 10000);
		globalThis[Symbol.for("openclaw.whatsapp.rawSock")] = sock;
		inboundLogger.info({}, "[label-probe] installed");
	} catch (e) { try { inboundLogger.warn({ error: String(e) }, "[label-probe] setup failed"); } catch {} }
	// --- END openclaw label-probe ---
	// --- BEGIN openclaw label-reconciler (customers.json -> WhatsApp labels) ---
	try {
		const __ocApplied = new Map();
		const __ocJidsFromPhone = (phone) => {
			// Label BOTH the LID jid and the phone jid: WhatsApp keys a chat by one or the
			// other depending on account/app version, and shows only associations on the
			// form it uses. The unused association is harmless.
			const digits = String(phone || "").replace(/\D/g, "");
			if (!digits) return [];
			const jids = [];
			try {
				const lid = JSON.parse(__ocReadFile(__ocHomedir() + "/.openclaw/credentials/whatsapp/default/lid-mapping-" + digits + ".json", "utf8"));
				if (lid) jids.push(String(lid) + "@lid");
			} catch {}
			jids.push(digits + "@s.whatsapp.net");
			return jids;
		};
		const __ocResolveCustomersPath = () => {
			try {
				const cfg = loadConfig();
				const ws = cfg?.agents?.defaults?.workspace;
				if (ws) return ws.replace(/\/$/, "") + "/data/customers.json";
			} catch {}
			return __ocHomedir() + "/wa-lead-gen/workspace/data/customers.json";
		};
		const __ocLoadLabelMap = () => {
			const map = {};
			try {
				const raw = JSON.parse(__ocReadFile(__ocHomedir() + "/.openclaw/whatsapp-labels.json", "utf8"));
				for (const k of Object.keys(raw)) {
					const lab = raw[k];
					if (lab && lab.name && !lab.deleted) {
						const name = String(lab.name).trim().toLowerCase();
						map[name === "complains" ? "complaints" : name] = String(lab.id);
					}
				}
			} catch {}
			return map;
		};
		let __ocReassert = 0;
		const __ocReconcile = async () => {
			let customers;
			try { customers = JSON.parse(__ocReadFile(__ocResolveCustomersPath(), "utf8")); } catch { return; }
			const list = customers && Array.isArray(customers.customers) ? customers.customers : [];
			if (!list.length) return;
			const labelMap = __ocLoadLabelMap();
			if (!Object.keys(labelMap).length) return;
			if (typeof sock.addChatLabel !== "function") return;
			const __ocCats = ["new customer", "important", "hot leads", "followup", "junk", "complaints", "ahsan", "ahmed", "imran", "rafay"];
			// Re-assert the current label every 20th cycle (~5 min) so a lost app-state
			// patch heals itself; other cycles send nothing unless the category changed.
			const reassert = __ocReassert++ % 20 === 0;
			const __ocTrack = globalThis[Symbol.for("openclaw.wa.chatLabels")];
			const __ocOps = globalThis[Symbol.for("openclaw.wa.labelOps")] || (globalThis[Symbol.for("openclaw.wa.labelOps")] = new Map());
			const __ocManagedIds = new Set();
			for (const k of __ocCats) if (labelMap[k]) __ocManagedIds.add(String(labelMap[k]));
			for (const c of list) {
				const digits = String((c && c.phone) || "").replace(/\D/g, "");
				const jids = __ocJidsFromPhone(c && c.phone);
				const cat = c && c.category ? String(c.category).trim().toLowerCase() : "";
				if (!digits || !jids.length || !__ocCats.includes(cat)) continue;
				const labelId = labelMap[cat] || null; // a category with no matching WhatsApp label just clears the others
				const changed = __ocApplied.get(digits) !== cat;
				try {
					// Strip managed labels the chat actually carries but shouldn't (double tags,
					// out-of-band tags like WhatsApp Business auto-"New customer"). Event-tracked,
					// so this sends nothing when the chat is already clean.
					if (typeof sock.removeChatLabel === "function") {
						for (const jid of jids) {
							const present = __ocTrack && __ocTrack.get(jid);
							if (!present) continue;
							for (const id of Array.from(present)) {
								const sid = String(id);
								if (__ocManagedIds.has(sid) && sid !== labelId) {
									try {
										__ocOps.set("remove:" + jid + ":" + sid, Date.now());
										await sock.removeChatLabel(jid, sid);
										present.delete(id);
										inboundLogger.info({ jid, labelId: sid }, "[label-reconciler] removed extra label");
									} catch {}
								}
							}
						}
					}
					if (!changed && !reassert) continue;
					for (const jid of jids) {
						if (labelId) { __ocOps.set("add:" + jid + ":" + labelId, Date.now()); await sock.addChatLabel(jid, labelId); }
						if (changed && typeof sock.removeChatLabel === "function") {
							// Blind swap on transition as a safety net for labels the tracker never saw
							for (const other of __ocCats) {
								const otherId = labelMap[other];
								if (otherId && otherId !== labelId) {
									try { __ocOps.set("remove:" + jid + ":" + otherId, Date.now()); await sock.removeChatLabel(jid, otherId); } catch {}
								}
							}
						}
					}
					__ocApplied.set(digits, cat);
					if (changed) inboundLogger.info({ jids, labelId, category: cat }, "[label-reconciler] applied label");
				} catch (e) { try { inboundLogger.warn({ jids, labelId, error: String(e) }, "[label-reconciler] apply failed"); } catch {} }
			}
			for (const [k, t] of __ocOps) if (Date.now() - t > 300000) __ocOps.delete(k); // prune stale echo entries
		};
		setTimeout(() => { __ocReconcile(); setInterval(__ocReconcile, 15000); }, 16000);
		inboundLogger.info({}, "[label-reconciler] installed");
	} catch (e) { try { inboundLogger.warn({ error: String(e) }, "[label-reconciler] setup failed"); } catch {} }
	// --- END openclaw label-reconciler ---
	// --- BEGIN openclaw media-queue sender (Baileys direct; CLI --media is broken) ---
	try {
		const __ocQueueFile = __ocHomedir() + "/.openclaw/wa-media-queue.jsonl";
		const __ocDoneFile = __ocHomedir() + "/.openclaw/wa-media-done.jsonl";
		// Start at the END of any existing queue so we never re-send jobs from
		// previous sessions on startup — only process jobs appended from now on.
		let __ocQOffset = 0;
		try { __ocQOffset = __ocReadFile(__ocQueueFile, "utf8").length; } catch {}
		const __ocProcessQueue = async () => {
			let content;
			try { content = __ocReadFile(__ocQueueFile, "utf8"); } catch { return; }
			if (content.length <= __ocQOffset) return;
			const chunk = content.slice(__ocQOffset);
			__ocQOffset = content.length;
			for (const line of chunk.split("\n")) {
				const trimmed = line.trim();
				if (!trimmed) continue;
				let job;
				try { job = JSON.parse(trimmed); } catch { continue; }
				try {
					const digits = String(job.to || "").replace(/\D/g, "");
					if (!digits) throw new Error("no recipient");
					const jid = digits + "@s.whatsapp.net";
					// Share a per-(chat,product) dedupe map with the auto-image safety net
					// so the same product image is never sent twice within 5 minutes.
					const __ocSt = globalThis[Symbol.for("openclaw.wa.autoimg")] || (globalThis[Symbol.for("openclaw.wa.autoimg")] = { sent: new Map(), catalog: null, at: 0 });
					if (job.productId) {
						const k = jid + "#" + job.productId;
						const last = __ocSt.sent.get(k);
						if (last && Date.now() - last < 300000) {
							try { __ocAppendFile(__ocDoneFile, JSON.stringify({ id: job.id, ok: true, deduped: true, ts: Date.now() }) + "\n"); } catch {}
							continue;
						}
						__ocSt.sent.set(k, Date.now());
					}
					let payload;
					if (job.image) {
						payload = { image: { url: job.image } };
						if (job.caption) payload.caption = job.caption;
					} else {
						payload = { text: job.caption || "" };
					}
					await sock.sendMessage(jid, payload);
					try { __ocAppendFile(__ocDoneFile, JSON.stringify({ id: job.id, ok: true, ts: Date.now() }) + "\n"); } catch {}
					inboundLogger.info({ to: digits, image: job.image || null }, "[media-queue] sent");
				} catch (e) {
					try { __ocAppendFile(__ocDoneFile, JSON.stringify({ id: job.id, ok: false, error: String(e) }) + "\n"); } catch {}
					try { inboundLogger.warn({ error: String(e) }, "[media-queue] send failed"); } catch {}
				}
			}
		};
		setInterval(() => { __ocProcessQueue(); }, 2000);
		inboundLogger.info({}, "[media-queue] watcher installed");
	} catch (e) { try { inboundLogger.warn({ error: String(e) }, "[media-queue] setup failed"); } catch {} }
	// --- END openclaw media-queue sender ---
	// --- BEGIN openclaw auto-image safety net (deterministic: send product image when bot mentions one) ---
	try {
		const __ocImgState = globalThis[Symbol.for("openclaw.wa.autoimg")] || (globalThis[Symbol.for("openclaw.wa.autoimg")] = { sent: new Map(), catalog: null, at: 0 });
		const __ocProductsFile = (() => {
			try { const ws = loadConfig()?.agents?.defaults?.workspace; if (ws) return ws.replace(/\/$/, "") + "/data/products.json"; } catch {}
			return __ocHomedir() + "/wa-lead-gen/workspace/data/products.json";
		})();
		const __ocCatalog = () => {
			const now = Date.now();
			if (__ocImgState.catalog && now - __ocImgState.at < 60000) return __ocImgState.catalog;
			try {
				const data = JSON.parse(__ocReadFile(__ocProductsFile, "utf8"));
				const map = {};
				for (const p of data.catalog || []) map[String(p.id)] = p;
				__ocImgState.catalog = map;
				__ocImgState.at = now;
				return map;
			} catch { return __ocImgState.catalog || {}; }
		};
		globalThis.__ocAutoSendImages = async (jid, content) => {
			try {
				if (!jid || !jid.endsWith("@s.whatsapp.net")) return; // customer DMs only
				const text = content && typeof content.text === "string" ? content.text : "";
				if (!text) return;
				const ids = new Set();
				for (const m of text.matchAll(/item-([A-Za-z0-9]+)/gi)) ids.add(m[1]);
				for (const m of text.matchAll(/\bItem\s+([A-Za-z0-9]+)\b/g)) ids.add(m[1]);
				if (!ids.size) return;
				const catalog = __ocCatalog();
				for (const pid of ids) {
					const prod = catalog[String(pid)];
					if (!prod || !prod.image) continue;
					const key = jid + "#" + pid;
					const last = __ocImgState.sent.get(key);
					if (last && Date.now() - last < 300000) continue; // already sent (by send_product or earlier)
					__ocImgState.sent.set(key, Date.now()); // optimistic claim to avoid races
					try {
						await sock.sendMessage(jid, { image: { url: prod.image } });
						inboundLogger.info({ to: jid, pid }, "[auto-image] sent");
					} catch (e) {
						try { inboundLogger.warn({ pid, error: String(e) }, "[auto-image] send failed"); } catch {}
					}
				}
			} catch (e) { try { inboundLogger.warn({ error: String(e) }, "[auto-image] handler error"); } catch {} }
		};
		inboundLogger.info({}, "[auto-image] safety net installed");
	} catch (e) { try { inboundLogger.warn({ error: String(e) }, "[auto-image] setup failed"); } catch {} }
	// --- END openclaw auto-image safety net ---
