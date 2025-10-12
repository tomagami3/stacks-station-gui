class DispMapper:
    def __init__(self):
        self.scale = 1.0
        self.xoff = 0
        self.yoff = 0
        self.W = 1
        self.H = 1

    def update(self, W, H, cw, ch):
        cw = max(1, int(cw))
        ch = max(1, int(ch))
        self.W, self.H = max(1, int(W)), max(1, int(H))
        self.scale = max(1e-6, min(cw / float(self.W), ch / float(self.H)))
        nw, nh = int(self.W * self.scale), int(self.H * self.scale)
        self.xoff = (cw - nw) // 2
        self.yoff = (ch - nh) // 2

    def c2i(self, xc, yc):
        xi = (xc - self.xoff) / (self.scale if self.scale else 1.0)
        yi = (yc - self.yoff) / (self.scale if self.scale else 1.0)
        xi = int(max(0, min(self.W - 1, xi)))
        yi = int(max(0, min(self.H - 1, yi)))
        return xi, yi

    def i2c(self, xi, yi):
        xc = int(self.xoff + xi * self.scale)
        yc = int(self.yoff + yi * self.scale)
        return xc, yc
