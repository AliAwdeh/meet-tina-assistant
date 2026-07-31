import { apiPost } from "../api/client";
import type { User } from "../types/domain";

type OptionsResponse<T> = { options: T };
type LoginResponse = {
  access_token: string;
  token_type: string;
  user: User;
};

type CredentialDescriptorJSON = Omit<PublicKeyCredentialDescriptor, "id"> & { id: string };
type CreationOptionsJSON = Omit<PublicKeyCredentialCreationOptions, "challenge" | "excludeCredentials" | "user"> & {
  challenge: string;
  excludeCredentials?: CredentialDescriptorJSON[];
  user: Omit<PublicKeyCredentialUserEntity, "id"> & { id: string };
};
type RequestOptionsJSON = Omit<PublicKeyCredentialRequestOptions, "allowCredentials" | "challenge"> & {
  allowCredentials?: CredentialDescriptorJSON[];
  challenge: string;
};

function base64urlToBuffer(value: string): ArrayBuffer {
  const normalized = value.replace(/-/g, "+").replace(/_/g, "/");
  const padded = normalized.padEnd(normalized.length + ((4 - (normalized.length % 4)) % 4), "=");
  const binary = window.atob(padded);
  const bytes = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index += 1) {
    bytes[index] = binary.charCodeAt(index);
  }
  return bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength);
}

function bufferToBase64url(buffer: ArrayBuffer | null): string | null {
  if (!buffer) return null;
  const bytes = new Uint8Array(buffer);
  let binary = "";
  for (const byte of bytes) {
    binary += String.fromCharCode(byte);
  }
  return window.btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/g, "");
}

function credentialSupported() {
  return typeof window !== "undefined" && "PublicKeyCredential" in window && "credentials" in navigator;
}

export function passkeysSupported() {
  return credentialSupported();
}

function creationOptionsFromJSON(options: CreationOptionsJSON): PublicKeyCredentialCreationOptions {
  return {
    ...options,
    challenge: base64urlToBuffer(options.challenge),
    excludeCredentials: options.excludeCredentials?.map((credential) => ({
      ...credential,
      id: base64urlToBuffer(credential.id)
    })),
    user: {
      ...options.user,
      id: base64urlToBuffer(options.user.id)
    }
  };
}

function requestOptionsFromJSON(options: RequestOptionsJSON): PublicKeyCredentialRequestOptions {
  return {
    ...options,
    allowCredentials: options.allowCredentials?.map((credential) => ({
      ...credential,
      id: base64urlToBuffer(credential.id)
    })),
    challenge: base64urlToBuffer(options.challenge)
  };
}

function credentialToJSON(credential: PublicKeyCredential) {
  const common = {
    id: credential.id,
    rawId: bufferToBase64url(credential.rawId),
    type: credential.type,
    authenticatorAttachment: credential.authenticatorAttachment
  };
  if (credential.response instanceof AuthenticatorAttestationResponse) {
    return {
      ...common,
      response: {
        attestationObject: bufferToBase64url(credential.response.attestationObject),
        clientDataJSON: bufferToBase64url(credential.response.clientDataJSON),
        transports: credential.response.getTransports?.() ?? []
      }
    };
  }
  if (credential.response instanceof AuthenticatorAssertionResponse) {
    return {
      ...common,
      response: {
        authenticatorData: bufferToBase64url(credential.response.authenticatorData),
        clientDataJSON: bufferToBase64url(credential.response.clientDataJSON),
        signature: bufferToBase64url(credential.response.signature),
        userHandle: bufferToBase64url(credential.response.userHandle)
      }
    };
  }
  throw new Error("Unsupported passkey response.");
}

export async function registerPasskey(deviceName: string) {
  if (!passkeysSupported()) throw new Error("This browser does not support passkeys.");
  const { options } = await apiPost<OptionsResponse<CreationOptionsJSON>>("/api/auth/passkeys/register/options", {});
  const credential = await navigator.credentials.create({ publicKey: creationOptionsFromJSON(options) });
  if (!(credential instanceof PublicKeyCredential)) throw new Error("Passkey setup was cancelled.");
  return apiPost("/api/auth/passkeys/register/verify", {
    credential: credentialToJSON(credential),
    device_name: deviceName
  });
}

export async function signInWithPasskey(email: string) {
  if (!passkeysSupported()) throw new Error("This browser does not support passkeys.");
  const { options } = await apiPost<OptionsResponse<RequestOptionsJSON>>("/api/auth/passkeys/login/options", { email });
  const credential = await navigator.credentials.get({ publicKey: requestOptionsFromJSON(options) });
  if (!(credential instanceof PublicKeyCredential)) throw new Error("Passkey sign in was cancelled.");
  return apiPost<LoginResponse>("/api/auth/passkeys/login/verify", {
    email,
    credential: credentialToJSON(credential)
  });
}
