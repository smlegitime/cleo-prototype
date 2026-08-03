/**
 * labeler-engine — the shared rule evaluator for CLEO labelers.
 *
 * One implementation of `(subject, spec) -> labels`, imported by the preview UI and (once
 * it exists) by the deployed labeler service that wraps @skyware/labeler's LabelerServer.
 * Deliberately dependency-free and platform-neutral: no React, no DOM, no I/O, no atproto.
 *
 * See ./evaluate.ts for why it is factored this way.
 */

export * from "./spec.js";
export * from "./evaluate.js";
