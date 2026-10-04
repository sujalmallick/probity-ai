import { fetchBlob, post, uploadFile } from "./api";

/** Upload a file and start an investigation. If the exact file was seen before, open that case instead. */
export async function launchCase(blob: Blob, name: string): Promise<{ caseId: string; duplicate: boolean }> {
  const doc = await uploadFile(blob, name);
  if (doc.duplicate_of && doc.duplicate_of.startsWith("case_")) return { caseId: doc.duplicate_of, duplicate: true };
  const c = await post<{ case_id: string }>("/cases", { document_id: doc.document_id });
  return { caseId: c.case_id, duplicate: false };
}

export async function launchDemoFile(name: string) {
  const blob = await fetchBlob(`/api/v1/demo/files/${encodeURIComponent(name)}`);
  return launchCase(blob, name);
}
