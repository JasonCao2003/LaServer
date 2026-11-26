import { EventEmitter } from 'node:events';
import { Server } from 'sky-enet';

export class EnetServer extends EventEmitter {
  constructor({ host, port, maxPeers, channels, logger }) {
    super();
    this.host = host;
    this.port = port;
    this.maxPeers = maxPeers;
    this.channels = channels;
    this.logger = logger;
    this.server = null;
  }

  async start() {
    if (this.server) {
      return;
    }

    this.server = await Server.create({
      ip: this.host,
      port: this.port,
      maxPeer: this.maxPeers,
      maxPeers: this.maxPeers,
      channelLimit: this.channels,
      checksum: true
    });

    this.server
      .on('ready', () => this.emit('ready'))
      .on('connect', event => this.emit('peer:connect', event.peer))
      .on('disconnect', event => this.emit('peer:disconnect', event.peer, event.data))
      .on('receive', event => this.emit('peer:message', event.peer, event.data, event.channelID))
      .on('error', error => {
        this.logger.error({ err: error }, 'enet transport error');
        this.emit('error', error);
      });

    // @note Server.listen() runs an infinite poll loop that only resolves when
    // stop() is called. Awaiting it would block bootstrap forever, so we run it
    // detached and resolve start() once the 'ready' event fires.
    await new Promise((resolve, reject) => {
      const onReady = () => {
        this.server.off('ready', onReady);
        this.server.off('error', onError);
        resolve();
      };

      const onError = error => {
        this.server.off('ready', onReady);
        this.server.off('error', onError);
        reject(error);
      };

      this.server.on('ready', onReady);
      this.server.on('error', onError);

      // @note kick off the poll loop without awaiting; surface unexpected exits.
      this.server.listen().catch(error => {
        this.logger.error({ err: error }, 'enet listen loop exited');
        this.emit('error', error);
      });
    });
  }

  async stop() {
    if (!this.server) {
      return;
    }

    this.server.stop();
    this.server.destroy?.();
    this.server = null;
  }

  send(peerId, buffer, { channel = 0, reliable = false } = {}) {
    if (!this.server) {
      return 0;
    }

    return this.server.send(peerId, channel, buffer, reliable);
  }

  disconnect(peerId, mode = 'now', data = 0) {
    if (!this.server) {
      return;
    }

    if (mode === 'later') {
      this.server.disconnectLater(peerId, data);
    } else if (mode === 'normal') {
      this.server.disconnect(peerId, data);
    } else {
      this.server.disconnectNow(peerId, data);
    }
  }
}
