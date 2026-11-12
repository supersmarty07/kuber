/* global frappe */

(function () {
	"use strict";

	const MAX_RECORDING_SECONDS = 30;
	const TARGET_SAMPLE_RATE = 16000;

	function encodePcm16Wav(float32Samples, sampleRate) {
		if (!float32Samples || !float32Samples.length) {
			throw new Error("No audio samples recorded");
		}
		if (sampleRate < 8000 || sampleRate > 48000) {
			throw new Error("Unsupported sample rate");
		}
		if (float32Samples.length > sampleRate * MAX_RECORDING_SECONDS) {
			throw new Error("Recording exceeds the 30-second limit");
		}
		const numFrames = float32Samples.length;
		const dataBytes = numFrames * 2;
		const buffer = new ArrayBuffer(44 + dataBytes);
		const view = new DataView(buffer);

		function writeAscii(offset, text) {
			for (let i = 0; i < text.length; i += 1) {
				view.setUint8(offset + i, text.charCodeAt(i));
			}
		}

		writeAscii(0, "RIFF");
		view.setUint32(4, 36 + dataBytes, true);
		writeAscii(8, "WAVE");
		writeAscii(12, "fmt ");
		view.setUint32(16, 16, true);
		view.setUint16(20, 1, true);
		view.setUint16(22, 1, true);
		view.setUint32(24, sampleRate, true);
		view.setUint32(28, sampleRate * 2, true);
		view.setUint16(32, 2, true);
		view.setUint16(34, 16, true);
		writeAscii(36, "data");
		view.setUint32(40, dataBytes, true);

		for (let i = 0; i < numFrames; i += 1) {
			const sample = Number(float32Samples[i]);
			const finiteSample = Number.isFinite(sample) ? sample : 0;
			const clamped = Math.max(-1, Math.min(1, finiteSample));
			const pcmValue = Math.round(clamped * 32767);
			view.setInt16(44 + i * 2, pcmValue, true);
		}
		return new Blob([buffer], { type: "audio/wav" });
	}

	function validateCashierSelection(previewResult, selectedByLine) {
		if (!previewResult || previewResult.status !== "review_required") {
			throw new Error("A reviewed preview response is required before building a draft");
		}
		const lines = Array.isArray(previewResult.items) ? previewResult.items : [];
		if (!lines.length) {
			throw new Error("No order lines available for draft creation");
		}
		const resolvedItems = lines.map((line, index) => {
			const choice = selectedByLine && selectedByLine[index];
			if (!choice || !choice.item_code || !choice.uom) {
				throw new Error(`Cashier must explicitly select item and UOM for line ${index + 1}`);
			}
			const allowedCodes = (line.candidates || []).map((candidate) => candidate.item_code);
			if (!allowedCodes.includes(choice.item_code)) {
				throw new Error(`Selected item ${choice.item_code} was not in the preview candidates`);
			}
			return {
				item_code: choice.item_code,
				quantity: String(choice.quantity || line.quantity),
				uom: String(choice.uom),
			};
		});
		return {
			items: resolvedItems,
			customer: (selectedByLine && selectedByLine.customer) || null,
			discount: (selectedByLine && selectedByLine.discount) || null,
			manager_approval_reference:
				(selectedByLine && selectedByLine.manager_approval_reference) || null,
		};
	}

	function applyDraftToPosCart(cartAdapter, reviewedDraft) {
		if (!reviewedDraft || reviewedDraft.status !== "draft_ready") {
			throw new Error("Only a server-verified draft_ready payload can be loaded into the cart");
		}
		if (reviewedDraft.can_submit !== false || reviewedDraft.requires_manual_checkout !== true) {
			throw new Error("Voice drafts must never auto-submit");
		}
		if (!cartAdapter || typeof cartAdapter.addReviewedLine !== "function") {
			throw new Error("A supported POS cart adapter with addReviewedLine is required");
		}
		if (reviewedDraft.customer && typeof cartAdapter.setCustomer === "function") {
			cartAdapter.setCustomer(reviewedDraft.customer);
		}
		for (const item of reviewedDraft.items) {
			cartAdapter.addReviewedLine({
				item_code: item.item_code,
				quantity: item.quantity,
				uom: item.uom,
				server_rate: item.server_rate,
			});
		}
		return {
			loaded_lines: reviewedDraft.items.length,
			order_revision: reviewedDraft.order_revision,
			auto_submitted: false,
		};
	}

	const exported = {
		MAX_RECORDING_SECONDS,
		TARGET_SAMPLE_RATE,
		encodePcm16Wav,
		validateCashierSelection,
		applyDraftToPosCart,
	};

	if (typeof window !== "undefined") {
		window.NepalRetailVoicePOS = exported;
	}
	if (typeof module !== "undefined" && module.exports) {
		module.exports = exported;
	}
})();
