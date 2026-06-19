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
		attachEmitterListener(sock.ev, "labels.association", (assoc) => {
			try { __ocAppendFile(__ocLabelDir + "/whatsapp-label-assoc.log", JSON.stringify(assoc) + "\n"); } catch {}
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
		const __ocJidFromPhone = (phone) => {
			const digits = String(phone || "").replace(/\D/g, "");
			if (!digits) return null;
			// WhatsApp "Lists" key chats by LID, not the phone JID. Resolve phone -> LID.
			try {
				const lid = JSON.parse(__ocReadFile(__ocHomedir() + "/.openclaw/credentials/whatsapp/default/lid-mapping-" + digits + ".json", "utf8"));
				if (lid) return String(lid) + "@lid";
			} catch {}
			return digits + "@s.whatsapp.net";
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
					if (lab && lab.name && !lab.deleted) map[String(lab.name).trim().toLowerCase()] = String(lab.id);
				}
			} catch {}
			return map;
		};
		const __ocReconcile = async () => {
			let customers;
			try { customers = JSON.parse(__ocReadFile(__ocResolveCustomersPath(), "utf8")); } catch { return; }
			const list = customers && Array.isArray(customers.customers) ? customers.customers : [];
			if (!list.length) return;
			const labelMap = __ocLoadLabelMap();
			if (!Object.keys(labelMap).length) return;
			const __ocCats = ["new customer", "important", "hot leads"];
			for (const c of list) {
				const jid = __ocJidFromPhone(c && c.phone);
				const cat = c && c.category ? String(c.category).trim().toLowerCase() : "";
				const labelId = labelMap[cat];
				if (!jid || !labelId) continue;
				if (__ocApplied.get(jid) === cat) continue;
				try {
					if (typeof sock.addChatLabel === "function") {
						await sock.addChatLabel(jid, labelId);
						// Swap: remove the OTHER category labels so the chat reflects only the current category
						for (const other of __ocCats) {
							if (other === cat) continue;
							const otherId = labelMap[other];
							if (otherId && typeof sock.removeChatLabel === "function") {
								try { await sock.removeChatLabel(jid, otherId); } catch {}
							}
						}
						__ocApplied.set(jid, cat);
						inboundLogger.info({ jid, labelId, category: cat }, "[label-reconciler] applied label");
					}
				} catch (e) { try { inboundLogger.warn({ jid, labelId, error: String(e) }, "[label-reconciler] apply failed"); } catch {} }
			}
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