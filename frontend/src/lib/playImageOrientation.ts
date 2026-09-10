/** Verified display orientation by source bytes, never by a reusable visual ID.
 * Original images and material authorization remain unchanged. */
const orientationByHash: Readonly<Record<string, number>> = {
  '8d58d7a23bfb6ef450e81b6cca13adf1f71c820bec2f738f4531a304813ab281': 270,
  'c170c777cc75b807dc16dc936ed3ddc43d10f9fd2f828717f9d4266e4a12acee': 270,
  '1b661fb52d7bf449f56fc6a0762baabc377884987c55bd85a6dd9ca0f6dfa89d': 180,
  '92922344a23be504fa851abac816bc42b595315fee896b0aa5e50b0836635b67': 90,
  '7bbe22b7f9077c34d132dd2c7df47c5132c101dd3aa79e0d06cf07a21723ff5a': 90,
  '50a1f57b3dda3cefa8e52db8458ff8e45d7604eee123e4e52feff52a344fc719': 180,
  '4fa8433e1ae28af00f00abd529613d212dc5020dd645c88d996912edc2628659': 180,
  '55e12eb9a1734eb6ac3ed00196cbf0672d0fc221841356055fab2584df4d2dbc': 270,
  '046572f33b6a593eb0ef9d8f59abeb22105f502c6246636f452a2b402d23937d': 270,
  '9c707bfe248ffb7fc45835ef0ac1d7c83de2b62bdee095945800ae3e6b3b71c0': 270,
  '7ddc647b3ac6e8ec2aba222fab5ae93a076ea8ec41df70b81ca965a444e45d4c': 180,
  '635671104a1075c9d8e36aa25d5294fb8302a0cd4c55904a4f479eca458b54d4': 180,
  '6d89db2a639cd56c2552b679c5a78b79e1d01cbc96cd67d5dc8de5dce30ddeb4': 270,
  'd8e445bbb7293a88e1054bb6e71c2fc38e01c1201dfa0864478f31c4163ab146': 270,
  'ee08a159bdc820762e059275bc7089d623e5c0d5791fc4e17300175980bd25a9': 180,
};

export async function playImageRotation(blob: Blob): Promise<number> {
  const digest = await crypto.subtle.digest('SHA-256', await blob.arrayBuffer());
  const hash = Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, '0')).join('');
  return orientationByHash[hash] ?? 0;
}
